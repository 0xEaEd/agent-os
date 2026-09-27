/**
 * The trading RPC contract (wallet.* / trading.*), as the gateway returns it.
 * Amounts are decimal strings in human units unless the key ends in `Raw`;
 * money is USD as a number or null when no price is known. Nothing here is
 * computed: every derived figure lives in logic.ts.
 */

export type ChainId = 8453 | 4663

export interface Token {
  chainId: number
  address: string
  symbol: string
  name: string
  decimals: number
  logoUrl: string | null
  native: boolean
  verified: boolean
}

export interface SearchToken extends Token {
  priceUsd: number | null
  liquidityUsd: number | null
}

export interface Wallet {
  address: string
  label: string
  primary: boolean
  createdAt: number
  chains: number[]
}

export interface Balance {
  chainId: number
  token: Token
  raw: string
  amount: string
  priceUsd: number | null
  valueUsd: number | null
  change24hPct: number | null
  /** When the engine last read this row from the chain (ms). */
  updatedAt?: number
  /** Junk the engine keeps out of the portfolio; only present when asked for. */
  hidden?: boolean
}

/**
 * How the engine's last chain read of one wallet/chain went. Anything but
 * `ok` means the amounts on that chain are the last good values, not fresh.
 */
export interface ChainRead {
  chainId: number
  wallet: string
  status: 'ok' | 'partial' | 'failed'
  reason: string | null
  readAt: number
}

export type OrderStatus =
  | 'quoted'
  | 'awaiting_approval'
  | 'approved'
  | 'rejected'
  | 'expired'
  | 'submitted'
  | 'confirmed'
  | 'failed'

export type Initiator = 'manual' | 'agent' | 'external'

/** A swap trades through a provider; a send moves one token to an address;
 *  a revoke sets an ERC-20 allowance to zero. Older engines omit `kind`. */
export type OrderKind = 'swap' | 'send' | 'revoke'

export interface Order {
  orderId: string
  createdAt: number
  updatedAt: number
  chainId: number
  wallet: string
  kind?: OrderKind
  /** send: where the tokens go; revoke: the spender losing its allowance. */
  recipient?: string | null
  /** "Permit2", "Uniswap Universal Router" … when the engine knows the address. */
  recipientLabel?: string | null
  /** The legs of one multisend share this; decided and reported as one. */
  batchId?: string | null
  tokenIn: Token
  tokenOut: Token
  amountIn: string
  amountInRaw: string
  expectedOut: string | null
  minOut: string | null
  /** What the receipt actually delivered; null until the swap is confirmed. */
  receivedOut?: string | null
  valueUsd: number | null
  priceImpactPct: number | null
  gasUsd: number | null
  status: OrderStatus
  /** Why it failed, was rejected, or — while it waits — why it is asking
   *  (over the threshold, a heavy impact, the price moved since the quote). */
  reason: string | null
  initiator: Initiator
  sessionKey: string | null
  note: string | null
  txHash: string | null
  /** The ERC-20 approve that preceded the swap, when one was needed. */
  approvalTxHash?: string | null
  explorerUrl: string | null
  expiresAt: number | null
  /** A swap to native ETH on an L2 that delivered WETH instead names it here. */
  deliveredToken?: Token | null
  provider?: ProviderId
  /** The caller's idempotency key, when it gave one. */
  clientOrderId?: string | null
}

export type EntryKind = 'swap' | 'deposit' | 'withdraw' | 'approval' | 'gas' | 'unwrap'

/** A wrapped-ETH holding an L2 handed back: one click turns it into ETH. */
export function isWrappedEth(token: Pick<Token, 'symbol' | 'native'>): boolean {
  return !token.native && token.symbol.toUpperCase() === 'WETH'
}

export interface Entry {
  id: string
  ts: number
  chainId: number
  wallet: string
  kind: EntryKind
  txHash: string | null
  explorerUrl: string | null
  tokenIn: Token | null
  amountIn: string | null
  tokenOut: Token | null
  amountOut: string | null
  valueUsd: number | null
  gasUsd: number | null
  initiator: Initiator
  orderId: string | null
  note: string | null
}

export interface Holding {
  chainId: number
  wallet: string | null
  token: Token
  amount: string
  raw: string
  priceUsd: number | null
  valueUsd: number | null
  costUsd: number | null
  avgCostUsd: number | null
  unrealizedUsd: number | null
  unrealizedPct: number | null
  realizedUsd: number
  change24hPct: number | null
  allocationPct: number
  /** Junk the engine (or the user) hid; only present when asked for. Never counted. */
  hidden?: boolean
}

export interface Totals {
  valueUsd: number
  costUsd: number
  unrealizedUsd: number
  realizedUsd: number
  gasUsd: number
  change24hUsd: number | null
  change24hPct: number | null
}

export interface Portfolio {
  totals: Totals
  holdings: Holding[]
  /** Held junk tokens left out of `holdings` and the totals. */
  hiddenCount?: number
  /** Positions with no known price: held, listed, but worth nothing in the totals. */
  unpricedCount?: number
  wallets: { wallet: Wallet; totals: Totals; unpricedCount?: number }[]
  updatedAt: number
  syncing: boolean
}

export type GuardDecision = 'allow' | 'needs_approval' | 'blocked_daily_cap'

export interface Quote {
  quoteId: string
  routing: string
  chainId: number
  wallet: string
  tokenIn: Token
  tokenOut: Token
  amountIn: string
  amountOut: string
  /** The same figures in base units, as the confirm sends them back so the
   *  engine can refuse a swap whose price moved since this was read. */
  amountOutRaw?: string
  minOut: string
  minOutRaw?: string
  priceImpactPct: number | null
  gasUsd: number | null
  valueUsd: number | null
  /** One tokenIn priced in tokenOut, as a decimal string; null when there is
   *  no input amount to divide by. */
  rate: string | null
  slippagePct: number
  /** When the engine stops honouring this price (ms). */
  expiresAt: number
  provider?: ProviderId
  /** Provider-side notes (a clamped slippage, a route that could not be fully
   *  simulated), for the confirm sheet. */
  warnings?: string[]
  guard: {
    decision: GuardDecision
    spentTodayUsd: number
    dailyCapUsd: number
    thresholdUsd: number
  }
}

export type UnlockMode = 'auto' | 'manual'

export interface WalletStatus {
  initialized: boolean
  unlocked: boolean
  unlockMode: UnlockMode
  walletCount: number
  primary: string | null
  vaultPath: string
}

export interface ChainStatus {
  chainId: number
  key: string
  name: string
  native: string
  explorer: string
  rpcUrl: string
  healthy: boolean | null
}

/** Who routes and builds the swap. The aggregator is the default and needs no
 *  key; Uniswap is the fallback and needs one. */
export type ProviderId = 'aggregator' | 'uniswap'

/** Ordered as the engine orders them: the default first. */
export const PROVIDERS: readonly { id: ProviderId; label: string }[] = [
  { id: 'aggregator', label: 'AgentOS Aggregator' },
  { id: 'uniswap', label: 'Uniswap' },
]

export const DEFAULT_PROVIDER: ProviderId = 'aggregator'

export function providerLabel(id: string | null | undefined): string {
  return PROVIDERS.find((p) => p.id === id)?.label ?? (id ? String(id) : '')
}

export interface ProviderStatus {
  id: ProviderId
  label: string
  needsKey: boolean
  keyConfigured: boolean
  healthy: boolean | null
}

export interface TradingStatus {
  enabled: boolean
  apiKeyConfigured: boolean
  chains: ChainStatus[]
  limits: { approvalThresholdUsd: number; dailyCapUsd: number; approvalTtlSeconds: number }
  unlockMode: UnlockMode
  unlocked: boolean
  syncing: boolean
  lastSyncAt: number | null
  /** Older engines omit these; the app then assumes the default provider. */
  provider?: ProviderId
  providers?: ProviderStatus[]
}

export interface ProbeResult {
  ok: boolean
  latencyMs: number | null
  error: string | null
}

/** One close, in unix *seconds* — the units both producers emit. */
export interface ChartPoint {
  t: number
  c: number
}

export type ChartRange = '1h' | '6h' | '1d' | '1w' | 'all'

/** The figures above the line. Every one rides on a response the chart's own
 *  price lookup already made, so none of them costs a request. */
export interface ChartStats {
  priceUsd: number | null
  priceNative: number | null
  /** The pool's quote token, which a WETH/USDC pair reports as USDC — not
   *  necessarily the chain's gas coin. */
  quoteSymbol: string
  marketCapUsd: number | null
  /** The venue, as DexScreener names it: "Uniswap v4". */
  market: string | null
  /** Move across the selected range, not a fixed 24h. */
  changePct: number | null
}

export interface Chart {
  source: 'geckoterminal' | 'snapshots'
  range: ChartRange
  points: ChartPoint[]
  stats: ChartStats
}

export interface Limits {
  dailyCapUsd: number
  spentTodayUsd: number
  thresholdUsd: number
  approvalTtlSeconds: number
}

/** One ERC-20 allowance a wallet has granted, with the amount read live. */
export interface Allowance {
  chainId: number
  wallet: string
  token: Token
  spender: string
  spenderLabel: string | null
  spenderUrl: string | null
  allowanceRaw: string | null
  /** "unlimited", a human amount, or null when the read failed. */
  allowance: string | null
  unlimited: boolean
  readFailed: boolean
  balanceRaw: string | null
  balance: string | null
  /** What the spender could take right now: min(allowance, balance), in USD. */
  exposureUsd: number | null
  lastBlock: number
  lastTxHash: string | null
  explorerUrl: string | null
}

export interface AllowanceList {
  wallet: string
  chainId: number | null
  allowances: Allowance[]
  count: number
  unlimitedCount: number
  /** The engine is still walking older blocks; the list may grow. */
  scanning?: boolean
  scannedTo?: number | null
  scanFrom?: number | null
  head?: number
  chains?: {
    chainId: number
    count: number
    scanning?: boolean
    scannedTo: number | null
    scanFrom?: number | null
    head?: number
  }[]
}

/** One chain's head as the engine's RPC saw it a moment ago. */
export interface NetworkChain {
  chainId: number
  key: string
  name: string
  native: string
  rpcUrl: string
  healthy: boolean
  latencyMs: number | null
  blockNumber: number | null
  blockAgeS: number | null
  blockTimeS: number
  baseFeeGwei: number | null
  priorityFeeGwei: number | null
  error: string | null
}

export interface NetworkStatus {
  chains: NetworkChain[]
  checkedAt: number
}

export interface DecodedArg {
  type: string
  value: unknown
  unlimited?: boolean
}

export interface DecodedCall {
  selector: string
  function: string | null
  args: DecodedArg[]
  known: boolean
  words: number
}

export interface DecodedMovement {
  token: Token
  from: string
  to: string
  amountRaw: string
  amount: string
  logIndex: number
}

export interface DecodedGrant {
  token: Token
  owner: string
  spender: string
  spenderLabel: string | null
  amountRaw: string
  amount: string
  unlimited: boolean
  logIndex: number
}

export interface DecodedTx {
  hash: string | null
  from: string | null
  to: string | null
  valueWei: string
  nonce: number | null
  blockNumber: number | null
  status: 'pending' | 'success' | 'reverted'
  gasUsed: number | null
  gasPriceWei: string | null
  gasWei: string | null
}

export interface Decoded {
  chainId: number
  call: DecodedCall
  description: string
  to: string | null
  toLabel: string | null
  toToken: Token | null
  decoded: {
    function: string
    token: Token
    counterparty: string
    counterpartyLabel: string | null
    amountRaw: string
    amount: string
    unlimited: boolean
  } | null
  tx: DecodedTx | null
  transfers: DecodedMovement[]
  approvals: DecodedGrant[]
  /** The vault's wallets that took part. */
  wallets: string[]
  explorerUrl: string | null
}

/** The zero address the API uses for the chain's native coin. */
export const NATIVE_ADDRESS = '0x0000000000000000000000000000000000000000'

export const CHAINS: readonly {
  id: ChainId
  key: string
  name: string
  short: string
  /** Two letters for a chip when the ledger is narrow. */
  abbr: string
}[] = [
  { id: 8453, key: 'base', name: 'Base', short: 'Base', abbr: 'BA' },
  { id: 4663, key: 'robinhood', name: 'Robinhood Chain', short: 'Robinhood', abbr: 'RH' },
]
