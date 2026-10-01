import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { createRef } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useApprovals } from '@/services/approval-monitor'
import { useConnection } from '@/stores/connection'
import { useGateway } from '~/stores/gateway'
import type { DeskProps } from '~/views/trading/desk/useDeskInstruments'
import { ChatView } from './ChatView'

// Issue #3548: the console's Run modes (execution mode, Pilot Router, plan
// mode, session usage) were unreachable from the desktop chat. ChatView now
// mounts the console's Toolbar behind the composer's sliders button, for the
// live session, without the Visual effects switch (the desktop never runs the
// router-fx strip).
const KEY = 'agent:main:webchat:run-modes-test'
const rpc = {
  waitForConnection: vi.fn(async () => {}),
  call: vi.fn(async (method: string): Promise<unknown> => {
    if (method === 'config.get') {
      return {
        agentos_router: { enabled: true, rollout_phase: 'full' },
        permissions: { default_mode: '' },
      }
    }
    if (method === 'plan.mode.get') return { planMode: false }
    if (method === 'usage.status') {
      return {
        sessions: [{ sessionKey: KEY, model: 'claude-x', inputTokens: 1200, outputTokens: 340 }],
      }
    }
    if (method === 'commands.list_for_surface') return { commands: [] }
    return {}
  }),
  on: vi.fn(() => () => {}),
}
vi.mock('@/app/providers', () => ({ useRpc: () => rpc }))
vi.mock('@/views/chat/RoutePicker', () => ({ RoutePicker: () => null }))

vi.mock('@/views/chat/useTranscript', () => ({
  useTranscript: () => ({
    containerRef: createRef(),
    routerFxDockRef: createRef(),
    send: vi.fn(),
    abort: vi.fn(),
    busy: false,
    routerFxEnabled: false,
    setRouterFxEnabled: vi.fn(),
    history: [],
    runState: { status: 'idle', label: '' },
    pinnedToTail: true,
    scrollToTail: vi.fn(),
    isCompactInFlightForCurrentSession: () => false,
    setStreamIdlePausedForApproval: vi.fn(),
    setPendingDelegates: vi.fn(),
  }),
}))

vi.mock('~/views/trading/desk/useDeskInstruments', () => ({
  useDeskInstruments: () => ({
    region: null,
    dockAbove: null,
    seats: null,
    placeholder: undefined,
    still: false,
    modal: null,
    emptyHint: null,
    onFocusChange: () => {},
  }),
}))

function mount(desk: DeskProps | null = null) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MemoryRouter initialEntries={[`/sessions/${encodeURIComponent(KEY)}`]}>
      <QueryClientProvider client={client}>
        <Routes>
          <Route path="/sessions/:key?" element={<ChatView desk={desk} />} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

function openRunModes() {
  fireEvent.click(screen.getByRole('button', { name: 'Run modes' }))
  return screen.getByRole('dialog', { name: 'Run modes' })
}

const fetchMock = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(
  async () => new Response(JSON.stringify({ ok: true }), { status: 200 }),
)

beforeEach(() => {
  rpc.call.mockClear()
  fetchMock.mockClear()
  vi.stubGlobal('fetch', fetchMock)
  localStorage.clear()
  useApprovals.setState({ elevatedMode: '' })
  useConnection.getState().setState('connected')
  useGateway.setState({ status: { state: 'running', pid: 1, url: 'http://x', error: null } })
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('ChatView · Run modes', () => {
  it.each([
    ['Chat mode', null],
    [
      'Trade mode',
      { entering: false, onFirstSend: vi.fn(), onStartFresh: vi.fn() } as unknown as DeskProps,
    ],
  ])('shows the session controls without Visual effects in %s', async (_mode, desk) => {
    mount(desk)
    const popover = openRunModes()

    expect(within(popover).getByText('Execution mode')).toBeInTheDocument()
    const router = await within(popover).findByRole('checkbox', { name: 'Pilot Router' })
    await waitFor(() => expect(router).toBeChecked())
    expect(within(popover).getByRole('checkbox', { name: 'Plan mode' })).not.toBeChecked()
    expect(within(popover).queryByRole('checkbox', { name: /visual effects/i })).toBeNull()
    expect(within(popover).queryByText(/visual effects/i)).toBeNull()

    // Usage and plan state are read for the live session, not a default one.
    await within(popover).findByText('claude-x')
    expect(rpc.call).toHaveBeenCalledWith('usage.status', { sessionKey: KEY })
    expect(rpc.call).toHaveBeenCalledWith('plan.mode.get', { key: KEY })
  })

  it('the Plan mode switch sets plan mode on the live session', async () => {
    mount()
    const popover = openRunModes()
    fireEvent.click(await within(popover).findByRole('checkbox', { name: 'Plan mode' }))
    await waitFor(() =>
      expect(rpc.call).toHaveBeenCalledWith('plan.mode.set', { key: KEY, mode: 'on' }),
    )
  })

  it('enabling bypass goes through the confirm and reaches /api/elevated-mode', async () => {
    mount()
    const popover = openRunModes()
    fireEvent.click(within(popover).getByRole('button', { name: 'Approval prompts' }))

    const confirm = await screen.findByRole('alertdialog')
    const enable = within(confirm).getByRole('button', { name: /enable bypass/i })
    // A real click starts with a mousedown, which lands outside the popover's
    // DOM (the confirm is portalled). It must not tear the confirm down.
    fireEvent.mouseDown(enable)
    fireEvent.click(enable)

    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    const [url, init] = fetchMock.mock.calls[0]!
    expect(String(url)).toMatch(/\/api\/elevated-mode$/)
    expect(JSON.parse(String(init?.body))).toEqual({ sessionKey: KEY, mode: 'bypass' })
    expect(useApprovals.getState().elevatedMode).toBe('bypass')
    expect(screen.getByRole('button', { name: 'Run modes' })).toHaveAttribute(
      'aria-expanded',
      'true',
    )
  })
})
