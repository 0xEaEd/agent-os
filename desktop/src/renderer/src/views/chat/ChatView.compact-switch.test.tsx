import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useNavigate, type NavigateFunction } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useConnection } from '@/stores/connection'
import type { UseSlashCommands } from '@/views/chat/useSlashCommands'
import { useGateway } from '~/stores/gateway'
import { ChatView } from './ChatView'

// A `/compact` marks its session's compaction in flight until the result or
// the gateway's frames settle it. Leave that session before either lands and
// both are lost to it (the frames are unsubscribed, the late result is
// dropped), so the flag has to go with the switch, as legacy's did
// (chat.js:1819-1820). Otherwise every send queues behind a compaction that
// already finished once the user comes back. The shared hooks run for real.
const A = 'agent:main:webchat:compact-a'
const B = 'agent:main:webchat:compact-b'
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
    // The compaction's answer never reaches this view.
    if (method === 'sessions.contextCompact') return new Promise(() => {})
    return {}
  }),
  on: vi.fn(() => () => {}),
}
vi.mock('@/app/providers', () => ({ useRpc: () => rpc }))
vi.mock('@/views/chat/RoutePicker', () => ({ RoutePicker: () => null }))

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

const nav: { go: NavigateFunction | null } = { go: null }
function Navigator() {
  // eslint-disable-next-line react-hooks/immutability -- a test handle on the router
  nav.go = useNavigate()
  return null
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MemoryRouter initialEntries={[`/sessions/${encodeURIComponent(A)}`]}>
      <QueryClientProvider client={client}>
        <Navigator />
        <Routes>
          <Route path="/sessions/:key?" element={<ChatView />} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

const sends = () => rpc.call.mock.calls.filter(([m]) => m === 'chat.send').length

beforeEach(() => {
  slash.execute = null
  rpc.call.mockClear()
  useConnection.getState().setState('connected')
  useGateway.setState({ status: { state: 'running', pid: 1, url: 'http://x', error: null } })
})

describe('ChatView · /compact across a session switch', () => {
  it('does not leave the session compacting after the user leaves and comes back', async () => {
    mount()
    await act(async () => {
      await slash.execute!('/compact')
    })
    expect(rpc.call).toHaveBeenCalledWith('sessions.contextCompact', { key: A })

    await act(async () => {
      await nav.go!(`/sessions/${encodeURIComponent(B)}`)
    })
    await act(async () => {
      await nav.go!(`/sessions/${encodeURIComponent(A)}`)
    })

    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'hello' } })
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /send/i }))
    })
    await waitFor(() => expect(sends()).toBe(1))
  })
})
