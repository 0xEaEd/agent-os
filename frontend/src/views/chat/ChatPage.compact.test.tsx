import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { toast } from 'sonner'
import { ChatPage } from './ChatPage'
import { KeyboardShortcutProvider } from '@/components/KeyboardShortcuts'

// Issue #3513: `/compact` cleared the composer and did nothing. The hook hands
// `compact_context` to the page's session-action delegate, which ignored it,
// and the hook's own RPC fallback only runs when there is no delegate.

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() },
}))

const KEY = 'agent:main:webchat:default'
const CATALOG = [
  {
    name: '/compact',
    usage: '/compact',
    description: 'Compact the context',
    aliases: [],
    execution: { action: 'compact_context' },
  },
]

type Settle = { resolve: (value: unknown) => void; reject: (err: Error) => void }
type Handler = (...args: unknown[]) => void

function makeRpc() {
  const compaction: { settle: Settle | null } = { settle: null }
  const listeners = new Map<string, Set<Handler>>()
  const rpc = {
    waitForConnection: vi.fn().mockResolvedValue(undefined),
    call: vi.fn((method: string): Promise<unknown> => {
      if (method === 'commands.list_for_surface') {
        return Promise.resolve({ surface: 'web_chat', commands: CATALOG })
      }
      if (method === 'sessions.contextCompact') {
        return new Promise((resolve, reject) => {
          compaction.settle = { resolve, reject }
        })
      }
      return Promise.resolve({})
    }),
    on: vi.fn((event: string, handler: Handler) => {
      if (!listeners.has(event)) listeners.set(event, new Set())
      listeners.get(event)!.add(handler)
      return () => listeners.get(event)?.delete(handler)
    }),
  }
  // The gateway's `session.event.compaction` broadcast to this session.
  const frame = (payload: Record<string, unknown>) =>
    listeners.get('session.event.compaction')?.forEach((h) => h(payload, {}))
  return { rpc, compaction, frame }
}
let mock = makeRpc()

vi.mock('@/app/providers', () => ({
  useRpc: () => mock.rpc,
  useBootstrap: () => ({
    version: '1',
    ws_url: 'ws://127.0.0.1:18791/ws',
    auth_mode: 'none',
    base_path: '/control',
    features: {},
  }),
}))

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/chat']}>
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
      >
        <KeyboardShortcutProvider>
          <ChatPage />
        </KeyboardShortcutProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

async function submit(text: string) {
  const ta = screen.getByRole('textbox') as HTMLTextAreaElement
  fireEvent.change(ta, { target: { value: text } })
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: /send/i }))
  })
}

async function runCompact() {
  renderPage()
  await waitFor(() =>
    expect(mock.rpc.call).toHaveBeenCalledWith('commands.list_for_surface', {
      surface: 'web_chat',
    }),
  )
  // A trailing space closes the slash menu, so Send runs the typed command.
  await submit('/compact ')
  await waitFor(() =>
    expect(mock.rpc.call).toHaveBeenCalledWith('sessions.contextCompact', { key: KEY }),
  )
}

const separator = () =>
  document.querySelector<HTMLElement>('.chat-context-separator--session') ?? null

const sends = () => mock.rpc.call.mock.calls.filter(([m]) => m === 'chat.send').length
const failedToasts = () =>
  vi.mocked(toast.error).mock.calls.filter(([msg]) => String(msg).startsWith('Compact failed'))
    .length

beforeEach(() => {
  mock = makeRpc()
  vi.mocked(toast.error).mockClear()
})

describe('ChatPage · /compact', () => {
  it('compacts the current session and marks it in flight until the result lands', async () => {
    await runCompact()
    expect(separator()?.dataset.status).toBe('started')
    expect(separator()?.textContent).toBe('context compacting')

    // While it runs, a message queues behind the compaction instead of racing it.
    await submit('hello')
    expect(sends()).toBe(0)

    // The gateway's result for a real session carries `compacted`, no status.
    await act(async () => {
      mock.compaction.settle!.resolve({ key: KEY, compacted: true, removed_count: 3 })
    })
    // The result settles the compaction, and the queued message goes out.
    await waitFor(() => expect(sends()).toBe(1))
  })

  it('shows a failed compaction on the separator', async () => {
    await runCompact()
    await act(async () => {
      mock.compaction.settle!.reject(new Error('context engine unavailable'))
    })
    expect(separator()?.dataset.status).toBe('failed')
    expect(separator()?.textContent).toBe('compaction failed')
    expect(failedToasts()).toBe(1)
  })

  it('reports a failure once when the broadcast frame settled it first', async () => {
    await runCompact()
    // The gateway publishes the lifecycle before the RPC returns.
    await act(async () => {
      mock.frame({
        key: KEY,
        source: 'manual',
        status: 'failed',
        user_visible: true,
        compaction_id: 'cmp_1',
        event: 'compaction.triggered',
      })
    })
    await act(async () => {
      mock.compaction.settle!.reject(new Error('context engine unavailable'))
    })
    expect(failedToasts()).toBe(1)
  })
})
