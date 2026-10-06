import { beforeEach, describe, expect, it } from 'vitest'
import type { Token } from '~/views/trading/types'
import { useTradingUi } from './trading-ui'

const NVDA: Token = {
  chainId: 4663,
  address: '0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec',
  symbol: 'NVDA',
  name: 'NVIDIA • Robinhood Token',
  decimals: 18,
  logoUrl: null,
  native: false,
  verified: true,
  stockToken: true,
}
const AI: Token = { ...NVDA, address: '0x2e8c4b1f0a3d6e7c9b5a4f3e2d1c0b9a8f7e1e18', symbol: 'AI' }

beforeEach(() => {
  localStorage.clear()
  useTradingUi.setState({
    bookTab: 'portfolio',
    bookOpen: false,
    deskMode: false,
    swapRequest: null,
  })
})

describe('trading-ui · requestSwap', () => {
  it('hands the pair over with a fresh seq and brings the BOOK up on Swap', () => {
    useTradingUi.getState().requestSwap({ chainId: 4663, tokenIn: NVDA, tokenOut: AI })
    const first = useTradingUi.getState()
    expect(first.swapRequest).toMatchObject({ chainId: 4663, tokenIn: NVDA, tokenOut: AI })
    expect(first.bookTab).toBe('swap')
    expect(first.bookOpen).toBe(true)
    expect(localStorage.getItem('agentos-desktop.trading.bookOpen')).toBe('true')

    // The same pair again is a new request: the ticket fills twice.
    useTradingUi.getState().requestSwap({ chainId: 4663, tokenIn: NVDA, tokenOut: AI })
    expect(useTradingUi.getState().swapRequest!.seq).toBeGreaterThan(first.swapRequest!.seq)
  })

  it('leaves the BOOK alone at the full desk, where the ticket is always on screen', () => {
    useTradingUi.setState({ deskMode: true })
    useTradingUi.getState().requestSwap({ chainId: 4663, tokenIn: NVDA })
    const s = useTradingUi.getState()
    expect(s.swapRequest).toMatchObject({ tokenIn: NVDA })
    expect(s.bookTab).toBe('portfolio')
    expect(s.bookOpen).toBe(false)
  })

  it('clears only the request the ticket took, never a newer one', () => {
    const ui = useTradingUi.getState()
    ui.requestSwap({ chainId: 4663, tokenIn: NVDA })
    const taken = useTradingUi.getState().swapRequest!.seq
    ui.requestSwap({ chainId: 4663, tokenIn: AI })
    ui.clearSwapRequest(taken)
    expect(useTradingUi.getState().swapRequest?.tokenIn.symbol).toBe('AI')
    ui.clearSwapRequest(useTradingUi.getState().swapRequest!.seq)
    expect(useTradingUi.getState().swapRequest).toBeNull()
    // A cleared request does not reset the counter.
    ui.requestSwap({ chainId: 4663, tokenIn: NVDA })
    expect(useTradingUi.getState().swapRequest!.seq).toBeGreaterThan(taken + 1)
  })
})

describe('trading-ui · Markets', () => {
  it('opens Markets on one token, on its chain, beside the chat', () => {
    useTradingUi.getState().openMarkets({ chainId: 4663, address: NVDA.address, symbol: 'NVDA' })
    const s = useTradingUi.getState()
    expect(s.bookTab).toBe('markets')
    expect(s.bookOpen).toBe(true)
    expect(s.markets).toMatchObject({ query: 'NVDA', address: NVDA.address, chainId: 4663 })
  })

  it('keeps filters across a patch', () => {
    useTradingUi.getState().setMarkets({ minTvlUsd: 100_000, deep: true })
    useTradingUi.getState().setMarkets({ query: 'TSLA', address: null })
    expect(useTradingUi.getState().markets).toMatchObject({
      query: 'TSLA',
      minTvlUsd: 100_000,
      deep: true,
      lookalikes: false,
    })
  })
})
