import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { toast } from 'sonner'
import { useSlashCommands } from './useSlashCommands'

// Issue #3513: `/model` built its list for a system row nobody passed a sink
// for, so only the title reached the user; and the router-hold commands set
// the hold behind the route chip's back, which kept its old label.

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() },
}))

const cmd = (name: string, action: string) => ({
  name,
  usage: name,
  description: name,
  aliases: [],
  execution: { action },
})
const CATALOG = [
  cmd('/model', 'models.list'),
  cmd('/c3', 'router.hold.set'),
  cmd('/use', 'router.hold.set'),
  cmd('/auto', 'router.hold.clear'),
]
const MODELS = [
  { id: 'claude-opus-5', name: 'Claude Opus 5', provider: 'anthropic', contextWindow: 200000 },
  { id: 'glm-4.6', name: 'GLM 4.6', provider: 'zai' },
]

let failHold = false
let models: Record<string, unknown>[] = MODELS
function makeRpc() {
  return {
    waitForConnection: vi.fn().mockResolvedValue(undefined),
    call: vi.fn(async (method: string) => {
      if (method === 'commands.list_for_surface') return { surface: 'web_chat', commands: CATALOG }
      if (method === 'models.list') return models
      if (method === 'router.hold.set' || method === 'router.hold.clear') {
        if (failHold) throw new Error('router off')
        return method === 'router.hold.set' ? { model: 'claude-opus-5' } : { cleared: true }
      }
      return {}
    }),
    on: vi.fn((): (() => void) => () => {}),
  }
}
let mockRpc = makeRpc()
vi.mock('@/app/providers', () => ({ useRpc: () => mockRpc }))

// The chat always mounts under a query client; the hook may read one.
function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

function setup(opts: Omit<Parameters<typeof useSlashCommands>[0] & object, 'sessionKey'> = {}) {
  return renderHook(() => useSlashCommands({ sessionKey: 'k', ...opts }), { wrapper })
}

async function run(result: ReturnType<typeof setup>['result'], text: string) {
  await waitFor(() => expect(result.current.commands.length).toBe(CATALOG.length))
  await act(async () => {
    expect(await result.current.execute(text)).toBe(true)
  })
}

const LIST = [
  '• Claude Opus 5 (claude-opus-5) — anthropic · 200k ctx',
  '• GLM 4.6 (glm-4.6) — zai',
].join('\n')

beforeEach(() => {
  failHold = false
  models = MODELS
  mockRpc = makeRpc()
  vi.mocked(toast.info).mockClear()
  vi.mocked(toast.error).mockClear()
})

describe('useSlashCommands · /model', () => {
  it('writes the whole list into the transcript', async () => {
    const addSystemMessage = vi.fn()
    const { result } = setup({ addSystemMessage })
    await run(result, '/model')
    await waitFor(() =>
      expect(addSystemMessage).toHaveBeenCalledWith(`Available models (2):\n${LIST}`),
    )
    expect(toast.info).not.toHaveBeenCalled()
  })

  it('without a transcript, puts the list on the toast rather than only its title', async () => {
    const { result } = setup()
    await run(result, '/model')
    await waitFor(() =>
      expect(toast.info).toHaveBeenCalledWith(
        'Available models (2):',
        expect.objectContaining({ description: LIST, style: { whiteSpace: 'pre-line' } }),
      ),
    )
  })

  it('caps the toast list and says how many more there are', async () => {
    models = Array.from({ length: 15 }, (_, i) => ({ id: `m${i}`, name: `M${i}`, provider: 'p' }))
    const { result } = setup()
    await run(result, '/model')
    await waitFor(() => expect(toast.info).toHaveBeenCalled())
    const [title, options] = vi.mocked(toast.info).mock.calls[0]!
    const rows = String((options as { description: string }).description).split('\n')
    expect(title).toBe('Available models (15):')
    expect(rows).toHaveLength(13)
    expect(rows[11]).toBe('• M11 (m11) — p')
    expect(rows[12]).toBe('…and 3 more (narrow with /model <filter>)')
  })
})

describe('useSlashCommands · route hold commands', () => {
  it.each([
    ['/c3', { key: 'k', tier: 'c3' }],
    ['/use glm-4.6', { key: 'k', model: 'glm-4.6' }],
  ])('%s tells the route chip to re-read the hold', async (text, params) => {
    const onRouteHoldChange = vi.fn()
    const { result } = setup({ onRouteHoldChange })
    await run(result, text)
    await waitFor(() => expect(onRouteHoldChange).toHaveBeenCalledTimes(1))
    expect(mockRpc.call).toHaveBeenCalledWith('router.hold.set', params)
  })

  it('/auto tells the route chip to re-read the hold', async () => {
    const onRouteHoldChange = vi.fn()
    const { result } = setup({ onRouteHoldChange })
    await run(result, '/auto')
    await waitFor(() => expect(onRouteHoldChange).toHaveBeenCalledTimes(1))
    expect(mockRpc.call).toHaveBeenCalledWith('router.hold.clear', { key: 'k' })
  })

  it.each(['/c3', '/use glm-4.6', '/auto'])(
    '%s leaves the chip alone when the gateway refuses',
    async (text) => {
      failHold = true
      const onRouteHoldChange = vi.fn()
      const { result } = setup({ onRouteHoldChange })
      await run(result, text)
      await waitFor(() => expect(toast.error).toHaveBeenCalled())
      expect(onRouteHoldChange).not.toHaveBeenCalled()
    },
  )
})
