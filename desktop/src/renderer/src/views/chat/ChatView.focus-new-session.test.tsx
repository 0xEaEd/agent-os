import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { createRef, useEffect } from 'react'
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { KeyboardShortcutProvider } from '@/components/KeyboardShortcuts'
import { useConnection } from '@/stores/connection'
import { useShellShortcuts } from '~/app/AppShell'
import { QuickActions } from '~/components/sidebar/QuickActions'
import { sessionPath } from '~/components/sidebar/SessionList'
import { useGateway } from '~/stores/gateway'
import { useTradingUi } from '~/stores/trading-ui'
import { useUi } from '~/stores/ui'
import type { DeskProps } from '~/views/trading/desk/useDeskInstruments'
import { ChatView } from './ChatView'

// Issue #3524: a new session keeps ChatView (and so the composer) mounted, and
// the composer only focused itself on mount. After ⌘N — or ⌘⇧O, `/new`, the
// pen button, the sidebar's New session — focus stayed wherever it was, and
// typing went nowhere.

const CHAT_KEY = 'agent:main:webchat:focus-chat'
const DESK_KEY = 'agent:trading:webchat:focus-desk'
const FRESH_DESK_KEY = 'agent:trading:webchat:focus-desk-fresh'

const rpc = {
  waitForConnection: vi.fn(async () => {}),
  call: vi.fn(async (): Promise<unknown> => ({})),
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

const seen = { path: '' }
const nav: { go: ((path: string) => void) | null } = { go: null }

/** The shell's shortcuts and the sidebar's quick actions, plus somewhere
    else in the window to hold focus. */
function Shell() {
  useShellShortcuts()
  const navigate = useNavigate()
  const { pathname } = useLocation()
  useEffect(() => {
    seen.path = pathname
  }, [pathname])
  useEffect(() => {
    nav.go = (path) => void navigate(path, { replace: true })
  }, [navigate])
  return (
    <>
      <button type="button" data-testid="outside">
        outside
      </button>
      <QuickActions />
    </>
  )
}

function mount(path: string, desk: DeskProps | null) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MemoryRouter initialEntries={[path]}>
      <QueryClientProvider client={client}>
        <KeyboardShortcutProvider>
          <Shell />
          <Routes>
            <Route path="/sessions/:key?" element={<ChatView desk={desk} />} />
          </Routes>
        </KeyboardShortcutProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

/** The desk as the shell sees it (views/trading/desk/TradingDesk): "start
    fresh" pauses the missions (a round-trip), then mints a new desk key,
    replaces the route with it and asks for the composer. ⌘N reaches it
    through the trading-ui store. */
function makeDesk() {
  const onStartFresh = vi.fn(async () => {
    await Promise.resolve()
    nav.go?.(sessionPath(FRESH_DESK_KEY))
    useUi.getState().requestComposerFocus()
  })
  useTradingUi.setState({ startFreshDesk: onStartFresh })
  return {
    desk: { entering: false, onFirstSend: vi.fn(), onStartFresh } as unknown as DeskProps,
    onStartFresh,
  }
}

const composer = () => document.querySelector('textarea')

/** Click-away: focus leaves the composer for something else in the window. */
function blurComposer() {
  const outside = screen.getByTestId('outside')
  act(() => outside.focus())
  expect(document.activeElement).toBe(outside)
}

function press(init: KeyboardEventInit) {
  act(() => {
    fireEvent.keyDown(document.activeElement ?? document, init)
  })
}

const pressNewSession = () => press({ key: 'n', code: 'KeyN', metaKey: true })
const pressNewChat = () => press({ key: 'o', code: 'KeyO', metaKey: true, shiftKey: true })

/** The desk's fresh session lands after its mission pause settles. */
async function settle() {
  await act(async () => {
    await Promise.resolve()
  })
}

function clickSidebarNew() {
  const link = screen.getByRole('link', { name: /New session/ })
  // A real click focuses the link first; fireEvent.click does not.
  act(() => link.focus())
  act(() => {
    fireEvent.click(link)
  })
}

beforeEach(() => {
  rpc.call.mockClear()
  useConnection.getState().setState('connected')
  useGateway.setState({ status: { state: 'running', pid: 1, url: 'http://x', error: null } })
})

afterEach(() => {
  useTradingUi.setState({ startFreshDesk: null })
})

describe('ChatView · a new session focuses the composer (Chat mode)', () => {
  it('⌘N from a session', () => {
    mount(sessionPath(CHAT_KEY), null)
    const before = composer()
    blurComposer()

    pressNewSession()

    expect(seen.path).toBe('/sessions')
    // Same node: the view stayed mounted, so this is not focus-on-mount.
    expect(composer()).toBe(before)
    expect(document.activeElement).toBe(composer())
  })

  it('⌘N on the keyless home, where the route does not change', () => {
    mount('/sessions', null)
    blurComposer()

    pressNewSession()

    expect(seen.path).toBe('/sessions')
    expect(document.activeElement).toBe(composer())
  })

  it('⌘⇧O from a session', () => {
    mount(sessionPath(CHAT_KEY), null)
    blurComposer()

    pressNewChat()

    expect(seen.path).toBe('/sessions')
    expect(document.activeElement).toBe(composer())
  })

  it('⌘⇧O on the keyless home', () => {
    mount('/sessions', null)
    blurComposer()

    pressNewChat()

    expect(seen.path).toBe('/sessions')
    expect(document.activeElement).toBe(composer())
  })

  it('the sidebar’s New session from a session', () => {
    mount(sessionPath(CHAT_KEY), null)

    clickSidebarNew()

    expect(seen.path).toBe('/sessions')
    expect(document.activeElement).toBe(composer())
  })

  it('the sidebar’s New session on the keyless home', () => {
    mount('/sessions', null)

    clickSidebarNew()

    expect(seen.path).toBe('/sessions')
    expect(document.activeElement).toBe(composer())
  })
})

describe('ChatView · landing on the keyless home focuses the composer', () => {
  it('any way there, e.g. deleting the open chat', () => {
    mount(sessionPath(CHAT_KEY), null)
    blurComposer()

    // session-actions.ts: deleting or archiving the open chat replaces the
    // route with the keyless home; nothing asks for the composer.
    act(() => nav.go?.('/sessions'))

    expect(seen.path).toBe('/sessions')
    expect(document.activeElement).toBe(composer())
  })
})

describe('ChatView · a fresh desk session focuses the composer (Trade mode)', () => {
  it('⌘N at the desk', async () => {
    const { desk, onStartFresh } = makeDesk()
    mount(sessionPath(DESK_KEY), desk)
    const before = composer()
    blurComposer()

    pressNewSession()
    await settle()

    expect(onStartFresh).toHaveBeenCalledTimes(1)
    expect(seen.path).toBe(sessionPath(FRESH_DESK_KEY))
    expect(composer()).toBe(before)
    expect(document.activeElement).toBe(composer())
  })

  it('⌘⇧O at the desk', async () => {
    const { desk, onStartFresh } = makeDesk()
    mount(sessionPath(DESK_KEY), desk)
    blurComposer()

    pressNewChat()
    await settle()

    expect(onStartFresh).toHaveBeenCalledTimes(1)
    expect(document.activeElement).toBe(composer())
  })

  it('the pen button at the desk', async () => {
    const { desk, onStartFresh } = makeDesk()
    mount(sessionPath(DESK_KEY), desk)
    const pen = screen.getByTestId('chat-fresh')
    // A real click focuses the button first; fireEvent.click does not.
    act(() => pen.focus())

    act(() => {
      fireEvent.click(pen)
    })
    await settle()

    expect(onStartFresh).toHaveBeenCalledTimes(1)
    expect(document.activeElement).toBe(composer())
  })
})
