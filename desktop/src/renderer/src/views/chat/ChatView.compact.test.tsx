import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render } from '@testing-library/react'
import { createRef } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useConnection } from '@/stores/connection'
import type { UseSlashCommands } from '@/views/chat/useSlashCommands'
import { useGateway } from '~/stores/gateway'
import type { DeskProps } from '~/views/trading/desk/useDeskInstruments'
import { ChatView } from './ChatView'

// Issue #3513: `/compact` was handed to the chat's session-action delegate,
// which only knew `new_chat`, so it did nothing in either mode. The delegate
// now runs the transcript's manual compaction (its in-flight state and
// separator are covered in the console's ChatPage.compact.test.tsx).
const KEY = 'agent:trading:webchat:compact-test'
const rpc = {
  waitForConnection: vi.fn(async () => {}),
  call: vi.fn(async (method: string): Promise<unknown> => {
    if (method === 'commands.list_for_surface') {
      return {
        commands: [
          {
            name: '/compact',
            usage: '/compact',
            description: 'Compact the context',
            aliases: [],
            execution: { action: 'compact_context' },
          },
        ],
      }
    }
    return {}
  }),
  on: vi.fn(() => () => {}),
}
vi.mock('@/app/providers', () => ({ useRpc: () => rpc }))
vi.mock('@/views/chat/RoutePicker', () => ({ RoutePicker: () => null }))

const compactContext = vi.fn()
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
    compactContext,
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

// The slash hook runs for real; the test only keeps a handle on `execute` so
// it can type a command the way the composer's Enter does.
const slash: { execute: UseSlashCommands['execute'] | null } = { execute: null }
vi.mock('@/views/chat/useSlashCommands', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/views/chat/useSlashCommands')>()
  return {
    ...real,
    useSlashCommands: (opts: Parameters<typeof real.useSlashCommands>[0]) => {
      const hook = real.useSlashCommands(opts)
      slash.execute = hook.execute
      return hook
    },
  }
})

function mount(desk: DeskProps | null) {
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

beforeEach(() => {
  slash.execute = null
  compactContext.mockClear()
  rpc.call.mockClear()
  useConnection.getState().setState('connected')
  useGateway.setState({ status: { state: 'running', pid: 1, url: 'http://x', error: null } })
})

describe('ChatView · /compact', () => {
  it.each([
    ['Chat mode', null],
    [
      'Trade mode',
      { entering: false, onFirstSend: vi.fn(), onStartFresh: vi.fn() } as unknown as DeskProps,
    ],
  ])('runs the manual compaction in %s', async (_mode, desk) => {
    mount(desk)
    await act(async () => {
      expect(await slash.execute!('/compact')).toBe(true)
    })
    expect(compactContext).toHaveBeenCalledTimes(1)
  })
})
