import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'
import { beforeEach, describe, expect, it } from 'vitest'
import { writeTradingSessionKey } from '~/stores/trading-ui'
import { TradingRedirect } from './TradingRedirect'

function Landing() {
  const loc = useLocation()
  return (
    <div data-testid="landing">
      {loc.pathname}
      {loc.search}|{JSON.stringify(loc.state)}
    </div>
  )
}

describe('TradingRedirect', () => {
  beforeEach(() => localStorage.clear())

  it('forwards /trading into the desk session with the query and the enter mark', () => {
    writeTradingSessionKey('agent:trading:webchat:trading-t1')
    render(
      <MemoryRouter initialEntries={['/trading?order=o9']}>
        <Routes>
          <Route path="/trading" element={<TradingRedirect />} />
          <Route path="/sessions/:key" element={<Landing />} />
        </Routes>
      </MemoryRouter>,
    )
    expect(screen.getByTestId('landing')).toHaveTextContent(
      '/sessions/agent%3Atrading%3Awebchat%3Atrading-t1?order=o9|{"enterDesk":true}',
    )
  })

  it('mints the desk session when none exists yet', () => {
    render(
      <MemoryRouter initialEntries={['/trading']}>
        <Routes>
          <Route path="/trading" element={<TradingRedirect />} />
          <Route path="/sessions/:key" element={<Landing />} />
        </Routes>
      </MemoryRouter>,
    )
    expect(screen.getByTestId('landing')).toHaveTextContent(
      /\/sessions\/agent%3Atrading%3Awebchat%3Atrading-/,
    )
  })
})
