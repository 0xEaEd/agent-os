/**
 * The Markets tab's arithmetic (docs/markets.md): which token a typed query
 * means, the words a row wears, and the Token objects a row's Swap hands to
 * the ticket. Nothing here renders.
 */

import { formatPrice, formatUsd } from './logic'
import {
  NATIVE_ADDRESS,
  type MarketsCounterparty,
  type MarketsPayload,
  type MarketsPool,
  type MarketsToken,
  type SearchToken,
  type Token,
} from './types'

export const ROBINHOOD_CHAIN = 4663

/** The Min TVL segments, in dollars; the engine's default is the middle one. */
export const MIN_TVL_STEPS = [1_000, 10_000, 100_000] as const
export const DEFAULT_MIN_TVL = 10_000

/** Rows a section shows before "+N more". */
export const SECTION_CAP = 40

export const ADDRESS_RE = /^0x[0-9a-fA-F]{40}$/

/** "$1k", "$10k", "$100k": the Min TVL segment labels. */
export function tvlStepLabel(usd: number): string {
  if (usd >= 1_000_000) return `$${usd / 1_000_000}M`
  if (usd >= 1_000) return `$${usd / 1_000}k`
  return `$${usd}`
}

const usdShort = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  notation: 'compact',
  maximumFractionDigits: 1,
})

/** TVL and volume for a narrow cell: "$4.7M", "$842K", "$12.3K"; under $1k as dollars. */
export function formatUsdShort(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  const abs = Math.abs(value)
  if (abs < 1_000) return formatUsd(value)
  const body = usdShort.format(abs)
  return value < 0 ? `−${body}` : body
}

/**
 * A pool's USD price: a memecoin at $0.1131 keeps its four significant
 * digits (the desk's `formatPrice` would round it to $0.11); under $0.001
 * the subscript notation, from $1 up ordinary dollars and cents.
 */
export function formatPoolPrice(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  const abs = Math.abs(value)
  if (abs === 0 || abs >= 1 || abs < 0.001) return formatPrice(value)
  const body = `$${Number(abs.toPrecision(4))}`
  return value < 0 ? `−${body}` : body
}

/**
 * One token priced in another, no currency sign: "240.3", "0.1131",
 * "0.0₃471" (the subscript counts the zeros, as prices do). Four significant
 * digits under 1, four decimals above, two past a thousand.
 */
export function formatRatio(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  const abs = Math.abs(value)
  if (abs === 0) return '0'
  if (abs < 0.001) return formatPrice(value).replace('$', '')
  const body =
    abs >= 1
      ? abs.toLocaleString('en-US', { maximumFractionDigits: abs >= 1000 ? 2 : 4 })
      : String(Number(abs.toPrecision(4)))
  return value < 0 ? `−${body}` : body
}

const MIN = 60
const HOUR = 60 * MIN
const DAY = 24 * HOUR

/**
 * A pool's age the way the card says it: "45m", "5h", "3d", "2mo", "1y".
 * Days run to a month so a two-week-old launch reads "14d", not "2w".
 */
export function poolAge(createdAt: string | null | undefined, now = Date.now()): string {
  if (!createdAt) return '—'
  const at = Date.parse(createdAt)
  if (!Number.isFinite(at)) return '—'
  const s = Math.max(0, Math.floor((now - at) / 1000))
  if (s < HOUR) return `${Math.max(1, Math.floor(s / MIN))}m`
  if (s < DAY) return `${Math.floor(s / HOUR)}h`
  if (s < 30 * DAY) return `${Math.floor(s / DAY)}d`
  if (s < 365 * DAY) return `${Math.floor(s / (30 * DAY))}mo`
  return `${Math.floor(s / (365 * DAY))}y`
}

/** "Uniswap v4", "Bankr": the engine's label, with the version when it knows one. */
export function dexLabel(dex: MarketsPool['dex']): string {
  const label = dex.label || dexIdLabel(dex.id)
  return dex.version ? `${label} ${dex.version}` : label
}

/** A readable name from a GeckoTerminal dex id when the label is missing: "ramses-v3-robinhood" → "Ramses". */
function dexIdLabel(id: string): string {
  const word = id.split('-')[0] ?? id
  return word ? word[0]!.toUpperCase() + word.slice(1) : id
}

/** "0.05%" from `feePct`; null when the pool name carried no fee. */
export function feeLabel(feePct: number | null): string | null {
  if (feePct === null || !Number.isFinite(feePct)) return null
  return `${Number(feePct.toFixed(4))}%`
}

/**
 * A Stock Token's name ends in "• Robinhood Token"; CoinGecko caps names at
 * 60 characters, so a long one arrives as "… • Robinhood Toke". Any prefix of
 * the suffix after the bullet counts; a name without the bullet never does.
 */
export function isStockTokenName(name: string | null | undefined): boolean {
  if (!name) return false
  const at = name.lastIndexOf('•')
  if (at < 0) return false
  const rest = name
    .slice(at + 1)
    .trim()
    .replace(/\s+/g, ' ')
  return rest.length >= 1 && 'Robinhood Token'.startsWith(rest)
}

/** A token the Markets tab is about: the engine's flag, or the Robinhood naming rule. */
export function isStockToken(token: Pick<Token, 'chainId' | 'name' | 'stockToken' | 'verified'>) {
  if (token.stockToken) return true
  return token.chainId === ROBINHOOD_CHAIN && token.verified && isStockTokenName(token.name)
}

export interface MarketsTarget {
  chainId: number
  /** An address when the lookup resolved one, else the query as typed (the engine resolves it). */
  target: string
  /** The resolved token, for the head line before the read lands. */
  token: SearchToken | null
}

/**
 * Which token a typed query means. An address is taken as given on `chain`.
 * A symbol is looked up on both chains (`results` from `useTokenSearch`):
 *
 * - With no chain chosen in the tab, a verified Stock Token of that symbol
 *   wins wherever it lives (NVDA typed on Base means Robinhood Chain's
 *   NVDA), then a verified match on the desk's chain, then on any chain.
 * - With a chain chosen, only that chain's verified match is taken.
 *
 * Nothing verified → the symbol goes to the engine as typed, on `chain`; it
 * applies its own Stock-Token-first rule and answers ambiguity itself.
 */
export function pickMarketsTarget(
  query: string,
  results: readonly SearchToken[],
  chain: number,
  explicitChain: boolean,
): MarketsTarget {
  const q = query.trim()
  if (ADDRESS_RE.test(q)) return { chainId: chain, target: q, token: null }
  const sym = q.toLowerCase()
  const exact = results.filter((r) => r.verified && r.symbol.toLowerCase() === sym)
  const onChain = exact.filter((r) => r.chainId === chain)
  const stockFirst = (rows: SearchToken[]) =>
    [...rows].sort((a, b) => Number(isStockToken(b)) - Number(isStockToken(a)))
  const pick = explicitChain
    ? stockFirst(onChain)[0]
    : (exact.find((r) => isStockToken(r)) ?? stockFirst(onChain)[0] ?? exact[0])
  if (!pick) return { chainId: chain, target: q, token: null }
  return { chainId: pick.chainId, target: pick.address, token: pick }
}

function asToken(
  chainId: number,
  t: Pick<
    MarketsToken | MarketsCounterparty,
    'address' | 'symbol' | 'name' | 'decimals' | 'logoUrl' | 'verified' | 'stockToken'
  >,
): Token {
  const native = t.address.toLowerCase() === NATIVE_ADDRESS
  return {
    chainId,
    address: t.address,
    symbol: t.symbol,
    name: t.name,
    decimals: t.decimals,
    logoUrl: t.logoUrl,
    native,
    verified: native || t.verified,
    stockToken: t.stockToken,
  }
}

/**
 * The two legs a row's Swap fills the ticket with, as full Token objects
 * (the ticket shows symbols and decimals before any quote). `pool.swap`
 * names the direction by address; each side is whichever of the token and
 * the counterparty carries that address.
 */
export function poolSwapTokens(
  payload: Pick<MarketsPayload, 'token'>,
  pool: Pick<MarketsPool, 'swap' | 'counterparty'>,
): { chainId: number; tokenIn: Token; tokenOut: Token } {
  const chainId = pool.swap.chainId
  const token = asToken(chainId, payload.token)
  const other = asToken(chainId, pool.counterparty)
  const isToken = (address: string) => address.toLowerCase() === token.address.toLowerCase()
  return isToken(pool.swap.tokenOut)
    ? { chainId, tokenIn: other, tokenOut: token }
    : { chainId, tokenIn: token, tokenOut: other }
}

/** The pool's two symbols, split for the row: ["AI", "NVDA"]. */
export function pairParts(pair: string): [string, string] {
  const at = pair.indexOf('/')
  if (at < 0) return [pair, '']
  return [pair.slice(0, at).trim(), pair.slice(at + 1).trim()]
}
