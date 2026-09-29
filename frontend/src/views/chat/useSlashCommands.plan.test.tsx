import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { toast } from 'sonner'
import { useSlashCommands } from './useSlashCommands'

// Issue #3513: the catalog serves `/plan` with `execution.action =
// "plan.mode.set"`, and the dispatch switch had no case for it, so `/plan` and
// `/plan off` fell through to the no-op default without a word.

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() },
}))

const KEY = 'agent:main:webchat:plan-test'
const CATALOG = [
  {
    name: '/plan',
    usage: '/plan [off]',
    description: 'Toggle plan mode',
    aliases: [],
    execution: { action: 'plan.mode.set' },
  },
]

// The gateway's plan flag, as `plan.mode.set` writes it and `plan.mode.get`
// reads it back.
let planMode = false
let failSet = false
function makeRpc() {
  return {
    waitForConnection: vi.fn().mockResolvedValue(undefined),
    call: vi.fn(async (method: string, params?: { key?: string; mode?: string }) => {
      if (method === 'commands.list_for_surface') return { surface: 'web_chat', commands: CATALOG }
      if (method === 'plan.mode.set') {
        if (failSet) throw new Error('gateway busy')
        planMode = params?.mode === 'on'
        return { key: params?.key, planMode }
      }
      if (method === 'plan.mode.get') return { key: params?.key, planMode }
      return {}
    }),
    on: vi.fn((): (() => void) => () => {}),
  }
}
let mockRpc = makeRpc()
vi.mock('@/app/providers', () => ({ useRpc: () => mockRpc }))

// The Toolbar's plan pill reads this same query; standing it up next to the
// hook shows whether the pill would follow the command.
function usePlanPill(sessionKey: string) {
  return useQuery<{ planMode?: boolean }>({
    queryKey: ['plan.mode.get', sessionKey],
    queryFn: () => mockRpc.call('plan.mode.get', { key: sessionKey }),
    retry: false,
    staleTime: 0,
  })
}

function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
  const hook = renderHook(
    () => ({ slash: useSlashCommands({ sessionKey: KEY }), pill: usePlanPill(KEY) }),
    { wrapper },
  )
  return hook
}

async function run(result: ReturnType<typeof setup>['result'], text: string) {
  await waitFor(() => expect(result.current.slash.commands.length).toBe(1))
  await act(async () => {
    expect(await result.current.slash.execute(text)).toBe(true)
  })
}

beforeEach(() => {
  planMode = false
  failSet = false
  mockRpc = makeRpc()
  vi.mocked(toast.info).mockClear()
  vi.mocked(toast.error).mockClear()
})

describe('useSlashCommands · /plan', () => {
  it('/plan turns plan mode on and the plan pill follows', async () => {
    const { result } = setup()
    await waitFor(() => expect(result.current.pill.data?.planMode).toBe(false))
    await run(result, '/plan')
    expect(mockRpc.call).toHaveBeenCalledWith('plan.mode.set', { key: KEY, mode: 'on' })
    await waitFor(() => expect(result.current.pill.data?.planMode).toBe(true))
    expect(toast.info).toHaveBeenCalledWith(
      'Plan mode on: research only until the plan is approved.',
    )
  })

  it.each(['off', 'OFF', 'exit', 'stop'])('/plan %s turns plan mode off', async (arg) => {
    planMode = true
    const { result } = setup()
    await waitFor(() => expect(result.current.pill.data?.planMode).toBe(true))
    await run(result, `/plan ${arg}`)
    expect(mockRpc.call).toHaveBeenCalledWith('plan.mode.set', { key: KEY, mode: 'off' })
    await waitFor(() => expect(result.current.pill.data?.planMode).toBe(false))
    expect(toast.info).toHaveBeenCalledWith('Plan mode off.')
  })

  it('says so when the gateway refuses, and re-reads the real flag', async () => {
    failSet = true
    const { result } = setup()
    await waitFor(() => expect(result.current.pill.data?.planMode).toBe(false))
    const reads = () => mockRpc.call.mock.calls.filter(([m]) => m === 'plan.mode.get').length
    const before = reads()
    await run(result, '/plan')
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Plan mode failed: gateway busy'))
    await waitFor(() => expect(reads()).toBeGreaterThan(before))
    expect(result.current.pill.data?.planMode).toBe(false)
  })
})
