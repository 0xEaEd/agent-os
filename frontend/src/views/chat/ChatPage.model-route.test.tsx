import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ChatPage } from './ChatPage'
import { KeyboardShortcutProvider } from '@/components/KeyboardShortcuts'

// Issue #3513: `/model` only toasted its title (the page passed the hook no
// system-row sink), and `/c0`–`/c3`, `/use`, `/auto` set the route hold without
// the composer's route chip hearing of it until a reload.

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

// The gateway's hold store for this session, as the router RPCs see it.
type Hold = { tier: string; model: string; targetType: 'tier' | 'model' } | null
let hold: Hold = null
const TIERS = [
  { tier: 'c0', model: 'glm-4.6' },
  { tier: 'c3', model: 'claude-opus-5' },
]

function makeRpc() {
  return {
    waitForConnection: vi.fn().mockResolvedValue(undefined),
    call: vi.fn(async (method: string, params?: Record<string, string>): Promise<unknown> => {
      switch (method) {
        case 'commands.list_for_surface':
          return { surface: 'web_chat', commands: CATALOG }
        case 'models.list':
          return [
            { id: 'claude-opus-5', name: 'Claude Opus 5', provider: 'anthropic' },
            { id: 'glm-4.6', name: 'GLM 4.6', provider: 'anthropic' },
          ]
        case 'router.hold.get':
          return { enabled: true, provider: 'anthropic', hold, tiers: TIERS, imageTiers: [] }
        case 'router.hold.set': {
          const tier = TIERS.find((row) => row.tier === params?.tier)
          hold = tier
            ? { ...tier, targetType: 'tier' }
            : { tier: 'c3', model: params?.model ?? '', targetType: 'model' }
          return { model: hold.model }
        }
        case 'router.hold.clear': {
          const cleared = hold !== null
          hold = null
          return { cleared }
        }
        default:
          return {}
      }
    }),
    on: vi.fn(() => () => {}),
  }
}
let mockRpc = makeRpc()

vi.mock('@/app/providers', () => ({
  useRpc: () => mockRpc,
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

async function ready() {
  renderPage()
  await waitFor(() =>
    expect(mockRpc.call).toHaveBeenCalledWith('commands.list_for_surface', {
      surface: 'web_chat',
    }),
  )
}

async function submit(text: string) {
  const ta = screen.getByRole('textbox') as HTMLTextAreaElement
  // A trailing space closes the slash menu, so Send runs the typed command.
  fireEvent.change(ta, { target: { value: `${text} ` } })
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: /send/i }))
  })
}

const chip = () => screen.getByRole('button', { name: 'Model route' })

beforeEach(() => {
  hold = null
  mockRpc = makeRpc()
})

describe('ChatPage · /model', () => {
  it('lists every model in the transcript', async () => {
    await ready()
    await submit('/model')
    await waitFor(() => expect(document.querySelector('.msg.system')).not.toBeNull())
    const row = document.querySelector('.msg.system .msg-body')!
    expect(row.textContent).toBe(
      [
        'Available models (2):',
        '• Claude Opus 5 (claude-opus-5) — anthropic',
        '• GLM 4.6 (glm-4.6) — anthropic',
      ].join('\n'),
    )
  })
})

describe('ChatPage · route hold commands', () => {
  it('the route chip follows /c3, /use and /auto without a reload', async () => {
    await ready()
    await waitFor(() => expect(chip()).toHaveTextContent('Auto'))

    await submit('/c3')
    await waitFor(() => expect(chip()).toHaveTextContent('c3 · claude-opus-5'))

    await submit('/use glm-4.6')
    await waitFor(() => expect(chip()).toHaveTextContent('glm-4.6'))
    expect(chip()).not.toHaveTextContent('c3')

    await submit('/auto')
    await waitFor(() => expect(chip()).toHaveTextContent('Auto'))
  })
})
