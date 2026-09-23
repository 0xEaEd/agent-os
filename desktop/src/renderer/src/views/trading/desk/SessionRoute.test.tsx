import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useGateway } from '~/stores/gateway'
import { writeTradingSessionKey } from '~/stores/trading-ui'
import { SessionRoute } from './SessionRoute'

const frameState = { fullDesk: true }
// The mock keeps the argument so the test can assert what the route hands the frame.
const useDeskFrame = vi.fn((input: unknown) => ({
  input,
  strip: <div data-testid="strip" />,
  banner: <div data-testid="frame-banner" />,
  desk: null,
  book: null,
  fullDesk: frameState.fullDesk,
  sheet: null,
  frameRef: { current: null },
  collapsed: false,
}))
vi.mock('./TradingDesk', () => ({ useDeskFrame: (input: unknown) => useDeskFrame(input) }))
vi.mock('../TradingView', () => ({
  TradingView: () => <div data-testid="trading-view" />,
}))
vi.mock('~/views/chat/ChatView', () => ({
  ChatView: () => <div data-testid="chat-view" />,
}))

const KEY = 'agent:trading:webchat:trading-t1'

function mount() {
  return render(
    <MemoryRouter initialEntries={[`/sessions/${encodeURIComponent(KEY)}`]}>
      <Routes>
        <Route path="/sessions/:key" element={<SessionRoute />} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  localStorage.clear()
  writeTradingSessionKey(KEY)
  useGateway.setState({ status: { state: 'running', pid: 1, url: 'http://x', error: null } })
  useDeskFrame.mockClear()
})

describe('SessionRoute', () => {
  it('lets the full desk carry its own gate: the frame banner is not shown twice', () => {
    frameState.fullDesk = true
    mount()
    expect(screen.getByTestId('trading-view')).toBeInTheDocument()
    expect(screen.queryByTestId('frame-banner')).toBeNull()
    expect(screen.getByTestId('strip')).toBeInTheDocument()
  })

  it('shows the frame banner over the chat + book layout', () => {
    frameState.fullDesk = false
    mount()
    expect(screen.getByTestId('chat-view')).toBeInTheDocument()
    expect(screen.getByTestId('frame-banner')).toBeInTheDocument()
  })

  it('hands the frame the derived mode, not a hardcoded one', () => {
    frameState.fullDesk = false
    mount()
    expect(useDeskFrame).toHaveBeenCalledWith(
      expect.objectContaining({ mode: 'trading', active: true, sessionKey: KEY }),
    )
  })
})
