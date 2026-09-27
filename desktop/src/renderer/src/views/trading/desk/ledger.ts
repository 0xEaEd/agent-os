import { formatAmount, formatUsd } from '../logic'
import { providerLabel, type OrderStatus, type ProviderId } from '../types'

/**
 * Trade ledger rows: what a `agentos trade …` / `agentos wallet …` call the
 * agent ran means in one line. Parsing is best-effort and total: a call we
 * cannot read still gets a row from its command line, and a result we cannot
 * parse leaves the raw block untouched.
 */

export type TradeKind =
  | 'quote'
  | 'swap'
  | 'send'
  | 'order'
  | 'orders'
  | 'approve'
  | 'reject'
  | 'allowances'
  | 'revoke'
  | 'decode'
  | 'network'
  | 'portfolio'
  | 'balances'
  | 'history'
  | 'status'
  | 'tokens'
  | 'limits'
  | 'sync'
  | 'wallet'
  | 'other'

export interface TradeCall {
  kind: TradeKind
  /** "Swap", "Quote", "Balances" … */
  title: string
  /** Argument summary from the command line, e.g. "0.01 ETH → USDC · Base". */
  detail: string
  command: string
}

const TRADE_RE = /(?:^|[\s;&|])(?:uv run\s+)?agentos\s+(trade|wallet)\s+([a-z-]+)((?:\s+\S+)*)/i

/** The command text out of an exec_command input (object or JSON string). */
export function commandFromToolInput(input: unknown): string | null {
  if (!input) return null
  if (typeof input === 'string') {
    const trimmed = input.trim()
    if (!trimmed) return null
    if (trimmed.startsWith('{')) {
      try {
        const parsed = JSON.parse(trimmed) as { command?: unknown }
        return typeof parsed.command === 'string' ? parsed.command : null
      } catch {
        // A truncated preview: pull the command field by hand.
        const m = /"command"\s*:\s*"((?:[^"\\]|\\.)*)/.exec(trimmed)
        return m ? m[1]!.replace(/\\"/g, '"').replace(/\\n/g, '\n') : null
      }
    }
    return trimmed
  }
  if (typeof input === 'object' && 'command' in (input as Record<string, unknown>)) {
    const c = (input as { command?: unknown }).command
    return typeof c === 'string' ? c : null
  }
  return null
}

function flag(args: string, name: string): string | null {
  const m = new RegExp(`--${name}(?:=|\\s+)("[^"]*"|'[^']*'|\\S+)`).exec(args)
  return m ? m[1]!.replace(/^['"]|['"]$/g, '') : null
}

/** Every value of a repeatable flag, e.g. the `--to` entries of a send. */
function flags(args: string, name: string): string[] {
  const re = new RegExp(`--${name}(?:=|\\s+)("[^"]*"|'[^']*'|\\S+)`, 'g')
  const out: string[] = []
  for (const m of args.matchAll(re)) out.push(m[1]!.replace(/^['"]|['"]$/g, ''))
  return out
}

function shortAddr(value: string): string {
  return /^0x[0-9a-fA-F]{40}$/.test(value) ? `${value.slice(0, 6)}…${value.slice(-4)}` : value
}

function chainWord(args: string): string {
  const c = (flag(args, 'chain') || '').toLowerCase()
  if (c === 'base' || c === '8453') return 'Base'
  if (c === 'robinhood' || c === '4663') return 'Robinhood'
  return ''
}

const TITLES: Record<TradeKind, string> = {
  quote: 'Quote',
  swap: 'Swap',
  send: 'Send',
  order: 'Order',
  orders: 'Orders',
  approve: 'Approve',
  reject: 'Reject',
  allowances: 'Allowances',
  revoke: 'Revoke',
  decode: 'Decode',
  network: 'Network',
  portfolio: 'Portfolio',
  balances: 'Balances',
  history: 'History',
  status: 'Desk status',
  tokens: 'Token search',
  limits: 'Limits',
  sync: 'Sync',
  wallet: 'Wallet',
  other: 'Trade call',
}

/** Recognise a desk command; null for anything else the agent ran. */
export function parseTradeCommand(command: string | null | undefined): TradeCall | null {
  if (!command) return null
  const m = TRADE_RE.exec(command)
  if (!m) return null
  const group = m[1]!.toLowerCase()
  const sub = m[2]!.toLowerCase()
  const args = m[3] ?? ''
  let kind: TradeKind = 'other'
  if (group === 'trade') {
    if (
      (
        [
          'quote',
          'swap',
          'send',
          'order',
          'orders',
          'approve',
          'reject',
          'allowances',
          'revoke',
          'decode',
          'network',
          'portfolio',
          'history',
          'status',
          'tokens',
          'limits',
          'sync',
        ] as const
      ).includes(sub as never)
    ) {
      kind = sub as TradeKind
    }
  } else if (sub === 'balances') kind = 'balances'
  else kind = 'wallet'

  let detail = ''
  if (kind === 'quote' || kind === 'swap') {
    const tin = flag(args, 'in')
    const tout = flag(args, 'out')
    const amount = flag(args, 'amount')
    const pct = flag(args, 'pct')
    const legs =
      tin && tout ? `${amount ? `${amount} ` : pct ? `${pct}% ` : ''}${tin} → ${tout}` : ''
    detail = [legs, chainWord(args)].filter(Boolean).join(' · ')
  } else if (kind === 'send') {
    const token = flag(args, 'token') ?? ''
    const to = flags(args, 'to')
    const amount = flag(args, 'amount')
    const usd = flag(args, 'usd')
    const size = amount ? `${amount} ${token}` : usd ? `$${usd} of ${token}` : token
    const who =
      to.length === 1
        ? `→ ${shortAddr(to[0]!.split('=')[0]!)}`
        : to.length > 1
          ? `→ ${to.length} recipients`
          : flag(args, 'file')
            ? '→ a list'
            : ''
    detail = [[size, who].filter(Boolean).join(' '), chainWord(args)].filter(Boolean).join(' · ')
  } else if (kind === 'revoke') {
    const token = flag(args, 'token')
    const spender = flag(args, 'spender')
    detail = [
      [token ? shortAddr(token) : '', spender ? `for ${shortAddr(spender)}` : '']
        .filter(Boolean)
        .join(' '),
      chainWord(args),
    ]
      .filter(Boolean)
      .join(' · ')
  } else if (kind === 'decode') {
    const hash = args
      .trim()
      .split(/\s+/)
      .find((a) => /^0x[0-9a-fA-F]{64}$/.test(a))
    detail = [
      hash ? `${hash.slice(0, 10)}…` : flag(args, 'data') ? 'calldata' : '',
      chainWord(args),
    ]
      .filter(Boolean)
      .join(' · ')
  } else if (kind === 'tokens') {
    const q = args
      .trim()
      .split(/\s+/)
      .filter((a) => !a.startsWith('--') && a !== chainWord(args).toLowerCase())
    detail = [q[q.length - 1] ?? '', chainWord(args)].filter(Boolean).join(' · ')
  } else if (kind === 'order' || kind === 'approve' || kind === 'reject') {
    const id = args
      .trim()
      .split(/\s+/)
      .find((a) => a && !a.startsWith('--'))
    detail = id ?? ''
  } else if (kind === 'wallet') {
    detail = sub
  } else {
    detail = chainWord(args)
  }
  return { kind, title: TITLES[kind], detail, command: command.trim() }
}

export interface TradeOutcome {
  summary: string
  status: OrderStatus | null
  orderId: string | null
  txHash: string | null
  explorerUrl: string | null
  provider: ProviderId | null
  /** Earned, not decorative: the result names a pending approval. */
  awaiting: boolean
  /** Earned: the result carries a confirmed status AND a tx hash. */
  confirmed: boolean
  error: string | null
  /** When the market figures in the result were read, if the result says. */
  marketAt: number | null
}

const EMPTY: TradeOutcome = {
  summary: '',
  status: null,
  orderId: null,
  txHash: null,
  explorerUrl: null,
  provider: null,
  awaiting: false,
  confirmed: false,
  error: null,
  marketAt: null,
}

type Dict = Record<string, unknown>
const isDict = (v: unknown): v is Dict => typeof v === 'object' && v !== null && !Array.isArray(v)
const str = (v: unknown): string | null => (typeof v === 'string' && v ? v : null)
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)
const sym = (v: unknown): string =>
  isDict(v) ? (str(v.symbol) ?? '') : typeof v === 'string' ? v : ''

/** The exit-code line exec_command puts first: `exit_code=1\n…`. */
export function exitCodeOf(text: string): number | null {
  const m = /^\s*exit_code=(-?\d+)/.exec(text)
  return m ? Number(m[1]) : null
}

/** The JSON document inside a tool result, ignoring the exit-code line and stray output. */
function parseJson(text: string): unknown {
  const start = text.search(/[{[]/)
  if (start < 0) return null
  const body = text.slice(start)
  const end = Math.max(body.lastIndexOf('}'), body.lastIndexOf(']'))
  if (end < 0) return null
  try {
    return JSON.parse(body.slice(0, end + 1))
  } catch {
    return null
  }
}

function orderLegs(o: Dict): string {
  const kind = str(o.kind) ?? 'swap'
  const amount = `${formatAmount(str(o.amountIn))} ${sym(o.tokenIn)}`
  if (kind === 'send') return `${amount} → ${shortAddr(str(o.recipient) ?? '')}`.trim()
  if (kind === 'revoke') {
    const who = str(o.recipientLabel) ?? shortAddr(str(o.recipient) ?? '')
    return `revoke ${sym(o.tokenIn)} for ${who}`.trim()
  }
  return `${amount} → ${
    str(o.expectedOut) ? `${formatAmount(str(o.expectedOut))} ` : ''
  }${sym(o.tokenOut)}`.trim()
}

function fromOrder(o: Dict): TradeOutcome {
  const status = str(o.status) as OrderStatus | null
  const txHash = str(o.txHash)
  const legs = orderLegs(o)
  const provider = (str(o.provider) as ProviderId | null) ?? null
  const bits = [legs]
  if (status) bits.push(statusWordFor(status))
  if (str(o.reason) && (status === 'rejected' || status === 'failed' || status === 'expired'))
    bits.push(String(o.reason))
  return {
    ...EMPTY,
    summary: bits.filter(Boolean).join(' · '),
    status,
    orderId: str(o.orderId),
    txHash,
    explorerUrl: str(o.explorerUrl),
    provider,
    awaiting: status === 'awaiting_approval',
    confirmed: status === 'confirmed' && Boolean(txHash),
    error: status === 'failed' ? (str(o.reason) ?? 'failed') : null,
  }
}

function statusWordFor(status: OrderStatus): string {
  switch (status) {
    case 'awaiting_approval':
      return 'awaiting approval'
    case 'submitted':
      return 'sent'
    default:
      return status
  }
}

/** One line for a result. Unknown shapes fall back to the first line of text. */
/** A projection marker the transcript substitutes for a result body it did not keep. */
const PROJECTION_MARKER = /^\s*\[[a-z_]+_projection\]\s*$/i

export function parseTradeResult(call: TradeCall, text: string): TradeOutcome {
  const data = parseJson(text)
  if (!isDict(data)) {
    const code = exitCodeOf(text)
    const lines = text
      .trim()
      .split('\n')
      .filter((l) => !/^\s*exit_code=/.test(l))
    // The result body was projected away: describe the call from its
    // arguments instead of leaking the marker, and never style it as an error.
    const firstLine = lines.find((l) => l.trim()) ?? ''
    if (PROJECTION_MARKER.test(firstLine)) {
      return { ...EMPTY, summary: call.detail }
    }
    const first = (lines[0] ?? '').slice(0, 140)
    if (code !== null && code !== 0)
      return { ...EMPTY, summary: first || `exit ${code}`, error: first || `exit ${code}` }
    return { ...EMPTY, summary: first }
  }
  if (isDict(data.error)) {
    const e = data.error
    return { ...EMPTY, summary: str(e.message) ?? 'error', error: str(e.message) ?? 'error' }
  }
  switch (call.kind) {
    case 'swap':
    case 'send': {
      const orders = Array.isArray(data.orders) ? data.orders.filter(isDict) : []
      if (orders.length === 1) return fromOrder(orders[0]!)
      if (orders.length > 1) {
        const awaiting = orders.some((o) => o.status === 'awaiting_approval')
        const confirmed = orders.every((o) => o.status === 'confirmed' && str(o.txHash))
        const counts = new Map<string, number>()
        for (const o of orders)
          counts.set(String(o.status), (counts.get(String(o.status)) ?? 0) + 1)
        const noun = call.kind === 'send' ? 'recipients' : 'wallets'
        const summary = `${orders.length} ${noun} · ${[...counts.entries()]
          .map(([s, n]) => `${n} ${statusWordFor(s as OrderStatus)}`)
          .join(', ')}`
        // One approval covers the batch: the stamp jumps to its first leg.
        const first = orders.find((o) => o.status === 'awaiting_approval') ?? orders[0]!
        return { ...EMPTY, summary, awaiting, confirmed, orderId: str(first.orderId) }
      }
      break
    }
    case 'order':
    case 'approve':
    case 'reject':
    case 'revoke': {
      if (isDict(data.order)) return fromOrder(data.order)
      break
    }
    case 'allowances': {
      const rows = Array.isArray(data.allowances) ? data.allowances.filter(isDict) : []
      const unlimited = num(data.unlimitedCount) ?? rows.filter((a) => a.unlimited).length
      const exposure = rows.reduce((s, a) => s + (num(a.exposureUsd) ?? 0), 0)
      const bits = [`${rows.length} live`]
      if (unlimited) bits.push(`${unlimited} unlimited`)
      if (exposure > 0) bits.push(`${formatUsd(exposure)} at stake`)
      return { ...EMPTY, summary: bits.join(' · '), error: null }
    }
    case 'decode': {
      const call_ = isDict(data.call) ? data.call : null
      const tx = isDict(data.tx) ? data.tx : null
      const bits: string[] = []
      const fn = call_ ? str(call_.function) : null
      bits.push(fn ? fn : call_ ? `${str(call_.selector) ?? '?'} (unknown)` : 'call')
      if (tx && str(tx.status)) bits.push(String(tx.status))
      const transfers = Array.isArray(data.transfers) ? data.transfers.length : 0
      if (transfers) bits.push(`${transfers} transfer${transfers === 1 ? '' : 's'}`)
      return { ...EMPTY, summary: bits.join(' · '), txHash: tx ? str(tx.hash) : null }
    }
    case 'network': {
      const rows = Array.isArray(data.chains) ? data.chains.filter(isDict) : []
      const parts = rows.map((c) => {
        const name = str(c.name) ?? str(c.key) ?? ''
        const age = num(c.blockAgeS)
        const ok = c.healthy === true
        return `${name} ${ok ? '✓' : '✗'}${age !== null ? ` ${age}s` : ''}`
      })
      const down = rows.filter((c) => c.healthy !== true).length
      return {
        ...EMPTY,
        summary: parts.join(' · '),
        error: down ? `${down} chain${down === 1 ? '' : 's'} unhealthy` : null,
      }
    }
    case 'quote': {
      const legs = `${formatAmount(str(data.amountIn))} ${sym(data.tokenIn)} → ${formatAmount(
        str(data.amountOut),
      )} ${sym(data.tokenOut)}`
      const impact = num(data.priceImpactPct)
      const bits = [legs]
      if (impact !== null) bits.push(`impact ${impact.toFixed(2)}%`)
      const guard = isDict(data.guard) ? str(data.guard.decision) : null
      if (guard === 'needs_approval') bits.push('would need approval')
      if (guard === 'blocked_daily_cap') bits.push('over the daily cap')
      return {
        ...EMPTY,
        summary: bits.join(' · '),
        provider: (str(data.provider) as ProviderId | null) ?? null,
      }
    }
    case 'portfolio': {
      const totals = isDict(data.totals) ? data.totals : null
      const value = totals ? num(totals.valueUsd) : null
      const holdings = Array.isArray(data.holdings) ? data.holdings.length : null
      const bits: string[] = []
      if (value !== null) bits.push(formatUsd(value))
      if (holdings !== null) bits.push(`${holdings} holdings`)
      return { ...EMPTY, summary: bits.join(' · '), marketAt: num(data.updatedAt) }
    }
    case 'balances': {
      const rows = Array.isArray(data.balances) ? data.balances.filter(isDict) : []
      const total = rows.reduce((s, b) => s + (num(b.valueUsd) ?? 0), 0)
      const nonZero = rows.filter((b) => Number(b.amount) > 0).length
      return {
        ...EMPTY,
        summary: `${nonZero} balances · ${formatUsd(total)}`,
        marketAt: num(data.updatedAt),
      }
    }
    case 'orders': {
      const rows = Array.isArray(data.orders) ? data.orders.filter(isDict) : []
      const pending = rows.filter((o) => o.status === 'awaiting_approval').length
      return {
        ...EMPTY,
        summary: `${rows.length} orders${pending ? ` · ${pending} awaiting approval` : ''}`,
        awaiting: pending > 0,
      }
    }
    case 'history': {
      const rows = Array.isArray(data.entries) ? data.entries.length : 0
      return { ...EMPTY, summary: `${rows} entries` }
    }
    case 'tokens': {
      const rows = Array.isArray(data.tokens) ? data.tokens.filter(isDict) : []
      const names = rows
        .slice(0, 3)
        .map((t) => `${sym(t)}${t.verified ? '' : '?'}`)
        .join(', ')
      return { ...EMPTY, summary: `${rows.length} matches${names ? ` · ${names}` : ''}` }
    }
    case 'status': {
      const provider = str(data.provider)
      const bits: string[] = []
      if (provider) bits.push(`via ${providerLabel(provider)}`)
      if (data.unlocked === true) bits.push('vault unlocked')
      if (data.unlocked === false) bits.push('vault locked')
      return { ...EMPTY, summary: bits.join(' · '), provider: provider as ProviderId | null }
    }
    case 'limits': {
      const left =
        num(data.dailyCapUsd) !== null && num(data.spentTodayUsd) !== null
          ? formatUsd((num(data.dailyCapUsd) ?? 0) - (num(data.spentTodayUsd) ?? 0))
          : null
      return { ...EMPTY, summary: left ? `${left} left today` : '' }
    }
    case 'sync':
      return { ...EMPTY, summary: data.started ? 'sync started' : '' }
    case 'wallet': {
      if (isDict(data.wallet))
        return {
          ...EMPTY,
          summary: `${str(data.wallet.label) ?? ''} ${str(data.wallet.address) ?? ''}`.trim(),
        }
      if (Array.isArray(data.wallets))
        return { ...EMPTY, summary: `${data.wallets.length} wallets` }
      if (typeof data.unlocked === 'boolean')
        return { ...EMPTY, summary: data.unlocked ? 'vault unlocked' : 'vault locked' }
      break
    }
    default:
      break
  }
  const first = text.trim().split('\n')[0] ?? ''
  return { ...EMPTY, summary: first.slice(0, 140) }
}

/** Runs of this many consecutive trade rows fold into one group. */
export const LEDGER_GROUP_MIN = 3

/** Split a sequence of row flags into runs; used to fold ≥3 consecutive rows. */
export function ledgerRuns(isTrade: readonly boolean[]): { start: number; length: number }[] {
  const runs: { start: number; length: number }[] = []
  let start = -1
  isTrade.forEach((flagged, i) => {
    if (flagged && start < 0) start = i
    if ((!flagged || i === isTrade.length - 1) && start >= 0) {
      const end = flagged ? i + 1 : i
      const length = end - start
      if (length >= LEDGER_GROUP_MIN) runs.push({ start, length })
      start = -1
    }
  })
  return runs
}
