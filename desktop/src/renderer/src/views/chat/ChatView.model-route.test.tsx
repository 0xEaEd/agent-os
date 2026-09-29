import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, waitFor } from '@testing-library/react'
import { createRef } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { toast } from 'sonner'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useConnection } from '@/stores/connection'
import type { UseSlashCommands } from '@/views/chat/useSlashCommands'
import { useGateway } from '~/stores/gateway'
import type { DeskProps } from '~/views/trading/desk/useDeskInstruments'
import { ChatView } from './ChatView'

// Issue #3513: `/model` only toasted its title, and `/c0`–`/c3`, `/use`,
// `/auto` left the composer's route chip on its old label. The chip itself is
// covered in the console's ChatPage.model-route.test.tsx; here, the desktop
// wiring in both modes.
vi.mock('sonner', () => ({
  toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() },
}))

const KEY = 'agent:trading:webchat:model-route-test'
const cmd = (name: string, action: string) => ({
  name,
  usage: name,
  description: name,
  aliases: [],
  execution: { action },
})
const rpc = {
  waitForConnection: vi.fn(async () => {}),
  call: vi.fn(async (method: string): Promise<unknown> => {
    if (method === 'commands.list_for_surface') {
      return {
        commands: [
          cmd('/model', 'models.list'),
          cmd('/c3', 'router.hold.set'),
          cmd('/use', 'router.hold.set'),
          cmd('/auto', 'router.hold.clear'),
        ],
      }
    }
    if (method === 'models.list') return [{ id: 'glm-4.6', name: 'GLM 4.6', provider: 'zai' }]
    if (method === 'router.hold.clear') return { cleared: true }
    return {}
  }),
  on: vi.fn(() => () => {}),
}
vi.mock('@/app/providers', () => ({ useRpc: () => rpc }))
vi.mock('@/views/chat/RoutePicker', () => ({ RoutePicker: () => null }))

const addSystemMessage = vi.fn()
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
    addSystemMessage,
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

function mount(desk: DeskProps | null, path = `/sessions/${encodeURIComponent(KEY)}`) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MemoryRouter initialEntries={[path]}>
      <QueryClientProvider client={client}>
        <Routes>
          <Route path="/sessions/:key?" element={<ChatView desk={desk} />} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

async function run(text: string) {
  await act(async () => {
    expect(await slash.execute!(text)).toBe(true)
  })
}

const holdReads = () => rpc.call.mock.calls.filter(([m]) => m === 'router.hold.get').length

const MODES = [
  ['Chat mode', null],
  [
    'Trade mode',
    { entering: false, onFirstSend: vi.fn(), onStartFresh: vi.fn() } as unknown as DeskProps,
  ],
] as const

beforeEach(() => {
  slash.execute = null
  addSystemMessage.mockClear()
  vi.mocked(toast.info).mockClear()
  rpc.call.mockClear()
  useConnection.getState().setState('connected')
  useGateway.setState({ status: { state: 'running', pid: 1, url: 'http://x', error: null } })
})

describe('ChatView · /model', () => {
  it.each(MODES)('writes the model list into the transcript in %s', async (_mode, desk) => {
    mount(desk)
    await run('/model')
    await waitFor(() =>
      expect(addSystemMessage).toHaveBeenCalledWith(
        'Available models (1):\n• GLM 4.6 (glm-4.6) — zai',
      ),
    )
  })

  it('on the keyless home, whose transcript is hidden, puts the list on the toast', async () => {
    mount(null, '/sessions')
    await run('/model')
    await waitFor(() =>
      expect(toast.info).toHaveBeenCalledWith(
        'Available models (1):',
        expect.objectContaining({ description: '• GLM 4.6 (glm-4.6) — zai' }),
      ),
    )
    expect(addSystemMessage).not.toHaveBeenCalled()
  })
})

describe('ChatView · route hold commands', () => {
  it.each(MODES)('re-reads the hold for the route chip in %s', async (_mode, desk) => {
    mount(desk)
    await waitFor(() => expect(holdReads()).toBe(1))
    for (const [text, reads] of [
      ['/c3', 2],
      ['/use glm-4.6', 3],
      ['/auto', 4],
    ] as const) {
      await run(text)
      await waitFor(() => expect(holdReads()).toBe(reads))
    }
  })
})
