import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import {
  dexLabel,
  feeLabel,
  formatPoolPrice,
  formatRatio,
  formatUsdShort,
  isStockToken,
  isStockTokenName,
  pairParts,
  pickMarketsTarget,
  poolAge,
  poolSwapTokens,
  tvlStepLabel,
} from './markets-logic'
import type { MarketsPayload, SearchToken } from './types'

const FIXTURE = 'src/renderer/src/views/trading/__fixtures__/markets/nvda.json'
const NVDA_MARKETS = JSON.parse(readFileSync(FIXTURE, 'utf8')) as MarketsPayload
const NVDA_ADDRESS = '0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec'

function found(extra: Partial<SearchToken>): SearchToken {
  return {
    chainId: 4663,
    address: NVDA_ADDRESS,
    symbol: 'NVDA',
    name: 'NVIDIA • Robinhood Token',
    decimals: 18,
    logoUrl: null,
    native: false,
    verified: true,
    stockToken: true,
    priceUsd: 240.3,
    liquidityUsd: null,
    ...extra,
  }
}

describe('markets-logic · words a row wears', () => {
  const now = Date.parse('2026-10-06T04:12:00Z')
  const ago = (s: number) => new Date(now - s * 1000).toISOString()

  it('says a pool’s age in minutes, hours, days, months, years', () => {
    expect(poolAge(ago(45 * 60), now)).toBe('45m')
    expect(poolAge(ago(5 * 3600), now)).toBe('5h')
    expect(poolAge(ago(3 * 86400), now)).toBe('3d')
    // Two weeks reads in days, not weeks: the card says "3d", "2mo".
    expect(poolAge(ago(14 * 86400), now)).toBe('14d')
    expect(poolAge(ago(73 * 86400), now)).toBe('2mo')
    expect(poolAge(ago(400 * 86400), now)).toBe('1y')
    expect(poolAge(null, now)).toBe('—')
    expect(poolAge('not a date', now)).toBe('—')
  })

  it('names the venue with its version, and falls back to the dex id', () => {
    expect(dexLabel({ id: 'uniswap-v4-robinhood', label: 'Uniswap', version: 'v4' })).toBe(
      'Uniswap v4',
    )
    expect(dexLabel({ id: 'bankr-robinhood', label: 'Bankr', version: null })).toBe('Bankr')
    expect(dexLabel({ id: 'ramses-v3-robinhood', label: '', version: 'v3' })).toBe('Ramses v3')
  })

  it('prints the fee tier, or nothing when the pool name had none', () => {
    expect(feeLabel(0.05)).toBe('0.05%')
    expect(feeLabel(0.025)).toBe('0.025%')
    expect(feeLabel(1)).toBe('1%')
    expect(feeLabel(null)).toBeNull()
  })

  it('labels the Min TVL steps', () => {
    expect([1_000, 10_000, 100_000].map(tvlStepLabel)).toEqual(['$1k', '$10k', '$100k'])
  })

  it('compacts TVL and volume past a thousand dollars', () => {
    expect(formatUsdShort(4_732_293)).toBe('$4.7M')
    expect(formatUsdShort(842_113)).toBe('$842.1K')
    expect(formatUsdShort(12_345)).toBe('$12.3K')
    expect(formatUsdShort(950)).toBe('$950.00')
    expect(formatUsdShort(null)).toBe('—')
  })

  it('keeps four significant digits on a sub-dollar pool price', () => {
    expect(formatPoolPrice(0.1131)).toBe('$0.1131')
    expect(formatPoolPrice(0.0042)).toBe('$0.0042')
    expect(formatPoolPrice(0.00001748)).toBe('$0.0₄1748')
    expect(formatPoolPrice(240.85)).toBe('$240.85')
    expect(formatPoolPrice(null)).toBe('—')
  })

  it('prints one token in another without a currency sign', () => {
    expect(formatRatio(0.000471)).toBe('0.0₃471')
    expect(formatRatio(0.1131)).toBe('0.1131')
    expect(formatRatio(0.0573)).toBe('0.0573')
    expect(formatRatio(240.85)).toBe('240.85')
    expect(formatRatio(2.78071)).toBe('2.7807')
    expect(formatRatio(1234.567)).toBe('1,234.57')
    expect(formatRatio(null)).toBe('—')
  })

  it('splits a pair into its two symbols', () => {
    expect(pairParts('AI/NVDA')).toEqual(['AI', 'NVDA'])
    expect(pairParts('NVDA / USDG')).toEqual(['NVDA', 'USDG'])
    expect(pairParts('ODD')).toEqual(['ODD', ''])
  })
})

describe('markets-logic · Stock Tokens', () => {
  it('reads the suffix the way the engine does, truncation included', () => {
    expect(isStockTokenName('NVIDIA • Robinhood Token')).toBe(true)
    expect(isStockTokenName('International Business Machines • Robinhood Toke')).toBe(true)
    expect(isStockTokenName('Some Long Company Name • Robinhood T')).toBe(true)
    // No bullet, no Stock Token, however it is spelt.
    expect(isStockTokenName(' NVIDIA Robinhood Token ')).toBe(false)
    expect(isStockTokenName('memestock TSLA')).toBe(false)
    expect(isStockTokenName('Fake • Coin')).toBe(false)
  })

  it('trusts the engine’s flag, and the name only for a verified Robinhood token', () => {
    expect(isStockToken({ chainId: 8453, name: 'x', verified: false, stockToken: true })).toBe(true)
    expect(isStockToken({ chainId: 4663, name: 'Apple • Robinhood Token', verified: true })).toBe(
      true,
    )
    expect(isStockToken({ chainId: 4663, name: 'Apple • Robinhood Token', verified: false })).toBe(
      false,
    )
    expect(isStockToken({ chainId: 8453, name: 'Apple • Robinhood Token', verified: true })).toBe(
      false,
    )
  })
})

describe('markets-logic · which token a query means', () => {
  const stock = found({})
  const lookalike = found({
    address: '0x1111111111111111111111111111111111111111',
    name: 'memestock NVDA',
    verified: false,
    stockToken: false,
  })
  const baseDegen = found({
    chainId: 8453,
    address: '0x4ed4e862860bed51a9570b96d89af5e1b0efefed',
    symbol: 'DEGEN',
    name: 'Degen',
    stockToken: false,
  })

  it('takes NVDA typed on Base to Robinhood Chain’s verified Stock Token', () => {
    const pick = pickMarketsTarget('nvda', [lookalike, stock], 8453, false)
    expect(pick).toMatchObject({ chainId: 4663, target: NVDA_ADDRESS })
    expect(pick.token?.symbol).toBe('NVDA')
  })

  it('keeps a chain the user chose, and hands the engine the symbol when it has no match there', () => {
    expect(pickMarketsTarget('NVDA', [stock], 8453, true)).toEqual({
      chainId: 8453,
      target: 'NVDA',
      token: null,
    })
  })

  it('falls back to a verified match on any chain, never to an unverified one', () => {
    expect(pickMarketsTarget('DEGEN', [baseDegen], 4663, false)).toMatchObject({
      chainId: 8453,
      target: baseDegen.address,
    })
    expect(pickMarketsTarget('NVDA', [lookalike], 4663, false)).toEqual({
      chainId: 4663,
      target: 'NVDA',
      token: null,
    })
  })

  it('takes an address as given, on the current chain', () => {
    expect(pickMarketsTarget(` ${NVDA_ADDRESS} `, [], 4663, false)).toEqual({
      chainId: 4663,
      target: NVDA_ADDRESS,
      token: null,
    })
  })
})

describe('markets-logic · a row’s Swap', () => {
  it('sells the token for the counterparty, as full Token objects for the ticket', () => {
    const ai = NVDA_MARKETS.sections.quote[0]!
    const legs = poolSwapTokens(NVDA_MARKETS, ai)
    expect(legs.chainId).toBe(4663)
    expect(legs.tokenIn).toEqual({
      chainId: 4663,
      address: NVDA_ADDRESS,
      symbol: 'NVDA',
      name: 'NVIDIA • Robinhood Token',
      decimals: 18,
      logoUrl: null,
      native: false,
      verified: true,
      stockToken: true,
    })
    expect(legs.tokenOut).toMatchObject({ symbol: 'AI', name: 'Artificial Inu', verified: false })
    expect(legs.tokenOut.address).toBe(ai.counterparty.address)
  })

  it('follows the direction `swap` names, whichever side the token is on', () => {
    const usdg = NVDA_MARKETS.sections.base[0]!
    const reversed = {
      ...usdg,
      swap: { chainId: 4663, tokenIn: usdg.counterparty.address, tokenOut: NVDA_ADDRESS },
    }
    const legs = poolSwapTokens(NVDA_MARKETS, reversed)
    expect(legs.tokenIn.symbol).toBe('USDG')
    expect(legs.tokenIn.decimals).toBe(6)
    expect(legs.tokenOut.symbol).toBe('NVDA')
  })
})
