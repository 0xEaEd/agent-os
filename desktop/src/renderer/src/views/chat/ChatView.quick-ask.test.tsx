import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen } from '@testing-library/react'
import { createRef } from 'react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useConnection } from '@/stores/connection'
import { STOPPED_GATEWAY } from '@shared/gateway'
import { desktopApi, resetDesktopApiForTests } from '~/lib/desktop-api'
import { QUICK_ASK_SETTLE_MS, useQuickAskRouting } from '~/lib/use-quick-ask'
import { useGateway } from '~/stores/gateway'
import { useQuickAsk } from '~/stores/quick-ask'
import { useUi } from '~/stores/ui'
import { ChatView } from './ChatView'

// Quick Ask (#3594): text typed in the global panel is SENT in the main
// window, in a new session (Return) or the one on screen (Option-Return),
// once that chat's transcript has settled.
const KEY = 'agent:main:webchat:quick-ask-test'
const atKey = `/sessions/${encodeURIComponent(KEY)}`

const rpc = {
  waitForConnection: vi.fn(async () => {}),
  call: vi.fn(async (): Promise<unknown> => ({})),
  on: vi.fn(() => () => {}),
}
vi.mock('@/app/providers', () => ({ useRpc: () => rpc }))
vi.mock('@/views/chat/RoutePicker', () => ({ RoutePicker: () => null }))

const transcript = {
  containerRef: createRef<HTMLDivElement>(),
  send: vi.fn(),
  busy: false,
}
vi.mock('@/views/chat/useTranscript', () => ({
  useTranscript: () => ({
    containerRef: transcript.containerRef,
    routerFxDockRef: { current: null },
    send: transcript.send,
    abort: vi.fn(),
    busy: transcript.busy,
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

function Where() {
  const { pathname } = useLocation()
  return <output data-testid="where">{pathname}</output>
}

/** The two halves as the app wires them: routing in the shell, sending in the chat. */
function Shell() {
  useQuickAskRouting()
  return (
    <>
      <ChatView />
      <Where />
    </>
  )
}

function mount(at: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MemoryRouter initialEntries={[at]}>
      <QueryClientProvider client={client}>
        <Routes>
          <Route path="/sessions/:key?" element={<Shell />} />
          <Route path="/projects/:id" element={<Shell />} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

const where = () => screen.getByTestId('where').textContent

/** What main does on Return in the panel: queue it, ping the window. */
async function quickAsk(text: string, target: 'new' | 'current') {
  await act(async () => {
    await desktopApi().quickAsk.submit({ text, target })
  })
}

/** The shared transcript's signal that the session's history has been drawn. */
async function settle() {
  await act(async () => {
    transcript.containerRef.current!.dataset.historyReady = 'true'
    await Promise.resolve()
  })
}

beforeEach(() => {
  delete window.agentos
  localStorage.clear()
  resetDesktopApiForTests()
  useQuickAsk.setState({ queue: [] })
  transcript.send.mockClear()
  transcript.busy = false
  useConnection.getState().setState('connected')
  useGateway.setState({ status: { state: 'running', pid: 1, url: 'http://x', error: null } })
})

afterEach(() => {
  vi.useRealTimers()
})

describe('ChatView · Quick Ask', () => {
  it('Option-Return sends to the session on screen once its history is drawn', async () => {
    mount(atKey)
    await quickAsk('and on Base?', 'current')
    expect(where()).toBe(atKey)
    // Not before the transcript has settled: its first draw would wipe the row.
    expect(transcript.send).not.toHaveBeenCalled()
    await settle()
    expect(transcript.send).toHaveBeenCalledTimes(1)
    expect(transcript.send).toHaveBeenCalledWith('and on Base?', [], null)
    expect(useQuickAsk.getState().queue).toEqual([])
  })

  it('Return goes to a fresh session and sends there', async () => {
    mount(atKey)
    await quickAsk('what moved ETH today?', 'new')
    expect(where()).toBe('/sessions')
    await settle()
    expect(transcript.send).toHaveBeenCalledWith('what moved ETH today?', [], null)
    // The first send gives the fresh session its URL, as a typed one does.
    expect(where()).toMatch(/^\/sessions\/agent%3Amain%3Awebchat%3A/)
    expect(where()).not.toBe(atKey)
  })

  it('off a chat, Option-Return goes to the last session opened', async () => {
    localStorage.setItem('agentos-desktop.lastSession', KEY)
    mount('/projects/p1')
    await quickAsk('follow up', 'current')
    expect(where()).toBe(atKey)
    await settle()
    expect(transcript.send).toHaveBeenCalledWith('follow up', [], null)
  })

  it('closes a sheet that covers the chat', async () => {
    useUi.getState().openSettings()
    mount(atKey)
    await quickAsk('hello', 'current')
    expect(useUi.getState().settingsOpen).toBe(false)
  })

  it('sends anyway when the history never settles', async () => {
    vi.useFakeTimers()
    mount(atKey)
    await quickAsk('still here', 'current')
    expect(transcript.send).not.toHaveBeenCalled()
    await act(async () => {
      vi.advanceTimersByTime(QUICK_ASK_SETTLE_MS)
    })
    expect(transcript.send).toHaveBeenCalledWith('still here', [], null)
  })

  it('queues behind a reply that is still streaming instead of dropping it', async () => {
    transcript.busy = true
    mount(atKey)
    await quickAsk('next question', 'current')
    await settle()
    expect(transcript.send).not.toHaveBeenCalled()
    expect(screen.getByText('next question')).toBeInTheDocument()
    expect(useQuickAsk.getState().queue).toEqual([])
  })

  it('holds the text while the gateway is down, says so, and sends once it is back', async () => {
    useGateway.setState({ status: { ...STOPPED_GATEWAY } })
    mount(atKey)
    await quickAsk('do not lose me', 'current')
    expect(screen.getByTestId('quick-ask-waiting')).toHaveTextContent('do not lose me')
    expect(transcript.send).not.toHaveBeenCalled()

    await act(async () => {
      useGateway.setState({ status: { state: 'running', pid: 1, url: 'http://x', error: null } })
    })
    await settle()
    expect(transcript.send).toHaveBeenCalledWith('do not lose me', [], null)
  })

  it('sends each of two quick submissions, in order', async () => {
    mount(atKey)
    await quickAsk('first', 'current')
    await quickAsk('second', 'current')
    await settle()
    // The second is routed after the first is sent; same chat, already settled.
    await act(async () => {
      await Promise.resolve()
    })
    expect(transcript.send.mock.calls.map((c) => c[0])).toEqual(['first', 'second'])
  })
})
