import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { createRef } from 'react'
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useConnection } from '@/stores/connection'
import type { UseSlashCommands } from '@/views/chat/useSlashCommands'
import { useGateway } from '~/stores/gateway'
import type { DeskProps } from '~/views/trading/desk/useDeskInstruments'
import { ChatView } from './ChatView'

// Issue #3513: in Trade mode, `/new` and ⌘⇧O went to the keyless Chat home,
// which is Chat mode, so the user left the desk. The header pen button already
// started a fresh desk session there. All three now take the same path.
const KEY = 'agent:trading:webchat:new-chat-test'
const rpc = {
  waitForConnection: vi.fn(async () => {}),
  call: vi.fn(async (method: string): Promise<unknown> => {
    if (method === 'commands.list_for_surface') {
      return {
        commands: [
          {
            name: '/new',
            usage: '/new',
            description: 'New chat',
            aliases: [],
            execution: { action: 'new_chat' },
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

const transcript = { send: vi.fn() }
vi.mock('@/views/chat/useTranscript', () => ({
  useTranscript: () => ({
    containerRef: createRef(),
    routerFxDockRef: createRef(),
    send: transcript.send,
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

// The shortcut provider is the shell's; here the chat's handler is kept by combo.
const shortcuts = new Map<string, (e: KeyboardEvent) => void>()
vi.mock('@/components/KeyboardShortcuts', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/components/KeyboardShortcuts')>()
  return {
    ...real,
    useKeyboardShortcut: (spec: { combo: string }, handler: (e: KeyboardEvent) => void) => {
      shortcuts.set(spec.combo, handler)
    },
  }
})

function Where() {
  const { pathname } = useLocation()
  return <output data-testid="where">{pathname}</output>
}

// Stands in for the sidebar: open another session without leaving the route.
const OTHER = 'agent:main:other-session'
function OpenOther() {
  const navigate = useNavigate()
  return (
    <button type="button" onClick={() => void navigate(`/sessions/${encodeURIComponent(OTHER)}`)}>
      open other
    </button>
  )
}

function mount(desk: DeskProps | null) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MemoryRouter initialEntries={[`/sessions/${encodeURIComponent(KEY)}`]}>
      <QueryClientProvider client={client}>
        <Routes>
          <Route
            path="/sessions/:key?"
            element={
              <>
                <ChatView desk={desk} />
                <Where />
                <OpenOther />
              </>
            }
          />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

const where = () => screen.getByTestId('where').textContent
const atKey = `/sessions/${encodeURIComponent(KEY)}`

function makeDesk() {
  return { entering: false, onFirstSend: vi.fn(), onStartFresh: vi.fn() }
}

async function typeSlash(text: string) {
  await act(async () => {
    await slash.execute!(text)
  })
}

function pressNewChat() {
  const event = new KeyboardEvent('keydown', { key: 'o', metaKey: true, shiftKey: true })
  act(() => shortcuts.get('mod+shift+o')!(event))
}

async function typeAndSend(text: string) {
  const ta = screen.getByRole('textbox')
  fireEvent.change(ta, { target: { value: text } })
  await act(async () => {
    fireEvent.keyDown(ta, { key: 'Enter' })
  })
}

beforeEach(() => {
  slash.execute = null
  transcript.send.mockClear()
  shortcuts.clear()
  rpc.call.mockClear()
  useConnection.getState().setState('connected')
  useGateway.setState({ status: { state: 'running', pid: 1, url: 'http://x', error: null } })
})

describe('ChatView · new chat in Chat mode', () => {
  it('/new goes to the keyless Chat home', async () => {
    mount(null)
    expect(where()).toBe(atKey)
    await typeSlash('/new')
    expect(where()).toBe('/sessions')
  })

  it('the new-chat shortcut goes to the keyless Chat home', () => {
    mount(null)
    pressNewChat()
    expect(where()).toBe('/sessions')
  })
})

// #3612: the view stays mounted across sessions, so a new chat's `new_chat`
// intent must not ride on the first send to a session that already exists —
// one opened from the sidebar, or a project folder's New chat, which creates
// its row before it navigates. The gateway rejects that as a conflict.
describe('ChatView · new chat intent', () => {
  it('rides on the first send from the keyless home', async () => {
    mount(null)
    pressNewChat()
    expect(where()).toBe('/sessions')
    await typeAndSend('hello')
    expect(transcript.send).toHaveBeenCalledTimes(1)
    expect(transcript.send).toHaveBeenCalledWith('hello', [], 'new_chat')
  })

  it('is dropped when another session opens before the first send', async () => {
    mount(null)
    pressNewChat()
    expect(where()).toBe('/sessions')
    fireEvent.click(screen.getByRole('button', { name: 'open other' }))
    expect(where()).toBe(`/sessions/${encodeURIComponent(OTHER)}`)
    await typeAndSend('hello')
    expect(transcript.send).toHaveBeenCalledTimes(1)
    expect(transcript.send).toHaveBeenCalledWith('hello', [], null)
  })
})

describe('ChatView · new chat in Trade mode', () => {
  it('/new starts a fresh desk session and stays on the desk', async () => {
    const desk = makeDesk()
    mount(desk as unknown as DeskProps)
    await typeSlash('/new')
    expect(desk.onStartFresh).toHaveBeenCalledTimes(1)
    expect(where()).toBe(atKey)
  })

  it('the new-chat shortcut starts a fresh desk session and stays on the desk', () => {
    const desk = makeDesk()
    mount(desk as unknown as DeskProps)
    pressNewChat()
    expect(desk.onStartFresh).toHaveBeenCalledTimes(1)
    expect(where()).toBe(atKey)
  })

  it('the header button still starts a fresh desk session', () => {
    const desk = makeDesk()
    mount(desk as unknown as DeskProps)
    const fresh = screen.getByTestId('chat-fresh')
    // The shortcut works at the desk now, so the button names it there too.
    expect(fresh.getAttribute('title')).toMatch(/\((⌘⇧O|Ctrl\+Shift\+O)\)$/)
    fireEvent.click(fresh)
    expect(desk.onStartFresh).toHaveBeenCalledTimes(1)
    expect(where()).toBe(atKey)
  })
})
