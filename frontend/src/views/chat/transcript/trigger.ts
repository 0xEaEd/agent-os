// Chat transcript — price trigger cards.
//
// `agentos trade trigger … --json` publishes a JSON artifact with the
// `application/vnd.agentos.trigger+json` mime; the artifact renderer emits a
// mount placeholder for it and this module fetches the payload and draws one
// of two layouts into it, keyed by the payload's `kind`: `trigger` (one
// conditional order, read like an instrument: the sentence, the live price
// against the line, a gauge) and `triggers` (the list). docs/triggers.md is
// the contract.
//
// Same two surfaces as dca.ts:
//   1. Pure helpers (top-level exports) — mime match, payload normalization,
//      hero / live-line / distance text, gauge geometry. No network.
//   2. `createTriggerMounter(deps)` — the imperative mounter the transcript
//      composes next to the DCA mounter.
//
// Styling: the markup carries `trigger-*` classes plus `data-trigger-kind`,
// `data-trigger-action`, `data-trigger-status`, `data-trigger-chain`,
// `data-trigger-layout` and `data-trigger-stale` hooks. The web console styles
// them in chat-unified.css; the desktop restyles the same hooks in its own
// chat.css.
//
// SECURITY: every payload-derived string reaches the DOM through `textContent`
// or an attribute setter, never `innerHTML` — token symbols are attacker-chosen
// on a permissionless chain. Explorer links are built from an http(s) explorer
// origin and a hex hash, or accepted only as an http(s) URL.

import { t, tPlural } from '@/i18n'
import '@/i18n/en/chat'

import type { Artifact } from './artifacts'
import {
  DCA_MINUTE_MS,
  DCA_SECOND_MS,
  dcaTickDelay,
  formatCountdown,
  formatDcaPrice,
  formatDcaUsd,
  statusReasonText,
} from './dca'
import {
  NO_VALUE,
  copyLpText,
  explorerUrl,
  formatTokenAmount,
  relativeTime,
  safeExplorerBase,
  shortAddress,
  type LpAmount,
  type LpChain,
  type LpToken,
  type LpWallet,
} from './lp'

/** The mime the engine publishes a price trigger read-out under. */
export const TRIGGER_ARTIFACT_MIME = 'application/vnd.agentos.trigger+json'

/** How long the copy button reads "copied". */
export const TRIGGER_COPIED_MS = 1_500

/** How long Stop / Fire now wait for their confirming second click. */
export const TRIGGER_CONFIRM_MS = 4_000

/** How long a failed ↻ says why under the footer. */
export const TRIGGER_ERROR_MS = 4_000

/** Card width (border box, px) from which a list row keeps its controls on the right. */
export const TRIGGER_WIDE_MIN_PX = 520

/** Rows in "Recent fires". */
export const TRIGGER_RECENT_FIRES = 5

/** An armed trigger within this many percent of its line reads "near". */
export const TRIGGER_NEAR_PCT = 1

/** The gauge's price axis reaches at least this far (a fraction of the line) past the data. */
export const TRIGGER_GAUGE_MIN_PAD = 0.02

const HOUR_MS = 3_600_000

/* ── Payload shape (docs/triggers.md) ───────────────────────────────────── */

export type TriggerKind = 'trigger' | 'triggers'
export type TriggerActionKind = 'sell' | 'buy' | 'alert' | 'unknown'
export type TriggerDirection = 'below' | 'above' | 'trail' | 'unknown'
export type TriggerStatus =
  | 'awaiting_approval'
  | 'armed'
  | 'triggered'
  | 'paused'
  | 'done'
  | 'stopped'
  | 'rejected'
  | 'expired'
  | 'unknown'
export type TriggerFireStatus =
  | 'pending'
  | 'filled'
  | 'parked'
  | 'alerted'
  | 'skipped'
  | 'failed'
  | 'expired'
  | 'rejected'
  | 'unknown'
/** A control on a trigger card; each calls `trading.trigger.<action>`. */
export type TriggerAction = 'approve' | 'reject' | 'pause' | 'resume' | 'fire' | 'stop'

export interface TriggerFire {
  n: number
  at: string
  manual: boolean
  status: TriggerFireStatus
  /** Machine code: insufficient_balance | needs_approval | trading.<code>. */
  reasonCode: string | null
  /** Human-readable detail. */
  reason: string | null
  priceUsd: number | null
  orderId: string | null
  txHash: string | null
  /** A safe http(s) link to the transaction, or ''. */
  explorerUrl: string
}

export interface TriggerCondition {
  direction: TriggerDirection
  priceUsd: number | null
  trailPct: number | null
  fromPriceUsd: number | null
  peakPriceUsd: number | null
  stopPriceUsd: number | null
  confirmTicks: number
  hits: number
  /** The engine's words ("under $3,800"), or '' (see `conditionLabel`). */
  label: string
}

export interface TriggerActionSpec {
  kind: TriggerActionKind
  amountUsd: number | null
  amountPct: number | null
  amount: LpAmount | null
  estimatedUsd: number | null
  slippagePct: number | null
  needsApproval: boolean
  approvalThresholdUsd: number | null
  dailyCapUsd: number | null
  label: string
}

export interface TriggerMarket {
  priceUsd: number | null
  armedPriceUsd: number | null
  /** Signed % move from the price needed to fire; 0 once met. */
  distancePct: number | null
  checkedAt: string | null
  balance: LpAmount | null
}

export interface TriggerResult {
  orderId: string
  txHash: string | null
  explorerUrl: string
  amountIn: LpAmount | null
  amountOut: LpAmount | null
  priceUsd: number | null
  gasUsd: number | null
}

export interface Trigger {
  id: string
  name: string
  kind: TriggerActionKind
  status: TriggerStatus
  statusReason: string | null
  chain: LpChain | null
  wallet: LpWallet | null
  token: LpToken
  quote: LpToken
  condition: TriggerCondition
  action: TriggerActionSpec
  market: TriggerMarket
  /** Newest first, ≤ 20. */
  fires: TriggerFire[]
  result: TriggerResult | null
  validUntil: string | null
  initiator: string
  sessionKey: string | null
  createdAt: string
  updatedAt: string
  approvedAt: string | null
  armedAt: string | null
  triggeredAt: string | null
  expiresAt: string | null
}

/** What ↻ re-runs: `trading.trigger.<kind>` with `params` verbatim. */
export interface TriggerRequest {
  kind: 'get' | 'list'
  params: Record<string, unknown>
}

interface TriggerEnvelope {
  version: number
  fetchedAt: string
  warnings: string[]
  request: TriggerRequest | null
}

export interface TriggerOnePayload extends TriggerEnvelope {
  kind: 'trigger'
  trigger: Trigger
  /** Only in the answer of `trading.trigger.fire`. */
  fire: TriggerFire | null
}

export interface TriggerListPayload extends TriggerEnvelope {
  kind: 'triggers'
  triggers: Trigger[]
  totals: { count: number; armed: number; awaiting: number; triggered: number }
}

export type TriggerPayload = TriggerOnePayload | TriggerListPayload

/* ── Pure helpers: mime + normalization ─────────────────────────────────── */

/** True when the artifact should render as a trigger card. */
export function isTriggerArtifact(artifact: Artifact | null | undefined): boolean {
  if (!artifact || !artifact.mime) return false
  return String(artifact.mime).toLowerCase().split(';')[0]?.trim() === TRIGGER_ARTIFACT_MIME
}

type Obj = Record<string, unknown>

function obj(value: unknown): Obj | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Obj) : null
}

function text(value: unknown): string {
  if (typeof value === 'string') return value.trim()
  if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  return ''
}

function textOrNull(value: unknown): string | null {
  return text(value) || null
}

/** A finite number, or null. Numeric strings count. */
function num(value: unknown): number | null {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

/** A positive finite number, or null: a price of 0 is no price. */
function price(value: unknown): number | null {
  const n = num(value)
  return n !== null && n > 0 ? n : null
}

function bool(value: unknown): boolean {
  return value === true || value === 'true' || value === 1
}

function list<T>(value: unknown, fn: (item: unknown) => T | null): T[] {
  return Array.isArray(value) ? value.map(fn).filter((item): item is T => item !== null) : []
}

function clamp01(value: number): number {
  return Math.min(1, Math.max(0, value))
}

function normChain(value: unknown): LpChain | null {
  const row = obj(value)
  if (!row) return null
  const key = text(row.key)
  const name = text(row.name) || key
  if (!name) return null
  return { id: num(row.id) ?? 0, key, name, explorer: safeExplorerBase(row.explorer) }
}

function normToken(value: unknown): LpToken | null {
  const row = obj(value)
  if (!row) return null
  const symbol = text(row.symbol)
  const address = text(row.address)
  if (!symbol && !address) return null
  return {
    address,
    symbol: symbol || shortAddress(address),
    decimals: num(row.decimals) ?? 0,
    priceUsd: num(row.priceUsd),
  }
}

function normAmount(value: unknown): LpAmount | null {
  const row = obj(value)
  if (!row) return null
  const human = text(row.human)
  const raw = text(row.raw)
  if (!human && !raw) return null
  return { raw, human, usd: num(row.usd) }
}

function normWallet(value: unknown): LpWallet | null {
  const row = obj(value)
  if (!row) return null
  const address = text(row.address)
  if (!address) return null
  return { address, label: textOrNull(row.label), inApp: row.inApp === true }
}

/** An http(s) URL as given, or ''. */
function safeHttpUrl(value: unknown): string {
  const raw = text(value)
  if (!raw) return ''
  try {
    const url = new URL(raw)
    return url.protocol === 'https:' || url.protocol === 'http:' ? url.href : ''
  } catch {
    return ''
  }
}

/** A tx link from the chain's explorer, else the payload's when it is plain http(s). */
function txLink(chain: LpChain | null, txHash: string | null, given: unknown): string {
  return (txHash ? explorerUrl(chain, 'tx', txHash) : '') || safeHttpUrl(given)
}

const STATUSES = new Set<TriggerStatus>([
  'awaiting_approval',
  'armed',
  'triggered',
  'paused',
  'done',
  'stopped',
  'rejected',
  'expired',
])

function normStatus(value: unknown): TriggerStatus {
  const raw = text(value).toLowerCase() as TriggerStatus
  return STATUSES.has(raw) ? raw : 'unknown'
}

const FIRE_STATUSES = new Set<TriggerFireStatus>([
  'pending',
  'filled',
  'parked',
  'alerted',
  'skipped',
  'failed',
  'expired',
  'rejected',
])

const ACTION_KINDS = new Set<TriggerActionKind>(['sell', 'buy', 'alert'])
const DIRECTIONS = new Set<TriggerDirection>(['below', 'above', 'trail'])

function normKind(...values: unknown[]): TriggerActionKind {
  for (const value of values) {
    const raw = text(value).toLowerCase() as TriggerActionKind
    if (ACTION_KINDS.has(raw)) return raw
  }
  return 'unknown'
}

function normDirection(value: unknown): TriggerDirection {
  const raw = text(value).toLowerCase() as TriggerDirection
  return DIRECTIONS.has(raw) ? raw : 'unknown'
}

function normFire(value: unknown, chain: LpChain | null): TriggerFire | null {
  const row = obj(value)
  if (!row) return null
  const status = text(row.status).toLowerCase() as TriggerFireStatus
  const txHash = textOrNull(row.txHash)
  return {
    n: Math.trunc(num(row.n) ?? 0),
    at: text(row.at),
    manual: bool(row.manual),
    status: FIRE_STATUSES.has(status) ? status : 'unknown',
    reasonCode: textOrNull(row.reasonCode),
    reason: textOrNull(row.reason),
    priceUsd: price(row.priceUsd),
    orderId: textOrNull(row.orderId),
    txHash,
    // Built from the chain's explorer when possible; the payload's link only
    // when it is plain http(s) — never a `javascript:` smuggled in a payload.
    explorerUrl: txLink(chain, txHash, row.explorerUrl),
  }
}

function normResult(value: unknown, chain: LpChain | null): TriggerResult | null {
  const row = obj(value)
  if (!row) return null
  const orderId = text(row.orderId)
  if (!orderId) return null
  const txHash = textOrNull(row.txHash)
  return {
    orderId,
    txHash,
    explorerUrl: txLink(chain, txHash, row.explorerUrl),
    amountIn: normAmount(row.amountIn),
    amountOut: normAmount(row.amountOut),
    priceUsd: price(row.priceUsd),
    gasUsd: num(row.gasUsd),
  }
}

/** Normalize one trigger, or null when it has no id or no token to name. */
export function normalizeTrigger(value: unknown): Trigger | null {
  const row = obj(value)
  if (!row) return null
  const id = text(row.id)
  const token = normToken(row.token)
  if (!id || !token) return null
  const quote = normToken(row.quote) ?? { address: '', symbol: '?', decimals: 0, priceUsd: null }
  const chain = normChain(row.chain)
  const condition = obj(row.condition) ?? {}
  const action = obj(row.action) ?? {}
  const market = obj(row.market) ?? {}
  const kind = normKind(row.kind, action.kind)
  const confirmTicks = Math.max(1, Math.trunc(num(condition.confirmTicks) ?? 2))
  const name = text(row.name)

  return {
    id,
    name: name || t('chat.triggerDefaultName', { token: token.symbol }),
    kind,
    status: normStatus(row.status),
    statusReason: textOrNull(row.statusReason),
    chain,
    wallet: normWallet(row.wallet),
    token,
    quote,
    condition: {
      direction: normDirection(condition.direction),
      priceUsd: price(condition.priceUsd),
      trailPct: price(condition.trailPct),
      fromPriceUsd: price(condition.fromPriceUsd),
      peakPriceUsd: price(condition.peakPriceUsd),
      stopPriceUsd: price(condition.stopPriceUsd),
      confirmTicks,
      hits: Math.min(confirmTicks, Math.max(0, Math.trunc(num(condition.hits) ?? 0))),
      label: text(condition.label),
    },
    action: {
      kind: normKind(action.kind, row.kind),
      amountUsd: price(action.amountUsd),
      amountPct: price(action.amountPct),
      amount: normAmount(action.amount),
      estimatedUsd: num(action.estimatedUsd),
      slippagePct: num(action.slippagePct),
      needsApproval: bool(action.needsApproval),
      approvalThresholdUsd: num(action.approvalThresholdUsd),
      dailyCapUsd: num(action.dailyCapUsd),
      label: text(action.label),
    },
    market: {
      priceUsd: price(market.priceUsd) ?? token.priceUsd,
      armedPriceUsd: price(market.armedPriceUsd),
      distancePct: num(market.distancePct),
      checkedAt: textOrNull(market.checkedAt),
      balance: normAmount(market.balance),
    },
    fires: list(row.fires, (item) => normFire(item, chain)),
    result: normResult(row.result, chain),
    validUntil: textOrNull(row.validUntil),
    initiator: text(row.initiator),
    sessionKey: textOrNull(row.sessionKey),
    createdAt: text(row.createdAt),
    updatedAt: text(row.updatedAt),
    approvedAt: textOrNull(row.approvedAt),
    armedAt: textOrNull(row.armedAt),
    triggeredAt: textOrNull(row.triggeredAt),
    expiresAt: textOrNull(row.expiresAt),
  }
}

/** The read to re-run, or null. Only `get` and `list` pass: the method is built from it. */
export function normalizeTriggerRequest(value: unknown): TriggerRequest | null {
  const row = obj(value)
  if (!row) return null
  const kind = text(row.kind)
  if (kind !== 'get' && kind !== 'list') return null
  return { kind, params: { ...(obj(row.params) ?? {}) } }
}

/**
 * Validate and normalize an artifact body into a renderable payload, or return
 * null when there is nothing to draw (unknown kind, no trigger). Never throws.
 */
export function normalizeTriggerPayload(raw: unknown): TriggerPayload | null {
  const body = obj(raw)
  if (!body) return null
  const kind = text(body.kind)
  const envelope: TriggerEnvelope = {
    version: num(body.version) ?? 1,
    fetchedAt: text(body.fetchedAt),
    warnings: list(body.warnings, (w) => text(w) || null),
    request: normalizeTriggerRequest(body.request),
  }
  if (kind === 'trigger') {
    const trigger = normalizeTrigger(body.trigger)
    if (!trigger) return null
    return { ...envelope, kind, trigger, fire: normFire(body.fire, trigger.chain) }
  }
  if (kind === 'triggers') {
    if (!Array.isArray(body.triggers)) return null
    const triggers = list(body.triggers, normalizeTrigger)
    const totals = obj(body.totals) ?? {}
    const count = (status: TriggerStatus): number =>
      triggers.filter((tr) => tr.status === status).length
    return {
      ...envelope,
      kind,
      triggers,
      totals: {
        count: Math.max(0, Math.trunc(num(totals.count) ?? triggers.length)),
        armed: Math.max(0, Math.trunc(num(totals.armed) ?? count('armed'))),
        awaiting: Math.max(0, Math.trunc(num(totals.awaiting) ?? count('awaiting_approval'))),
        triggered: Math.max(0, Math.trunc(num(totals.triggered) ?? count('triggered'))),
      },
    }
  }
  return null
}

/* ── Pure helpers: text ─────────────────────────────────────────────────── */

/** A percent figure without trailing zeros: 50 → "50", 12.5 → "12.5". */
function pctNumber(value: number): string {
  return String(Number(value.toFixed(2)))
}

/**
 * The condition in words: the engine's label, else one derived from the
 * direction ("under $3,800", "over $5,000", "10 % below peak").
 */
export function conditionLabel(trigger: Trigger): string {
  const c = trigger.condition
  if (c.label) return c.label
  switch (c.direction) {
    case 'below':
      return t('chat.triggerCondBelow', { price: formatDcaPrice(c.priceUsd) })
    case 'above':
      return t('chat.triggerCondAbove', { price: formatDcaPrice(c.priceUsd) })
    case 'trail':
      return t('chat.triggerCondTrail', {
        pct: c.trailPct !== null ? pctNumber(c.trailPct) : NO_VALUE,
      })
    default:
      return t('chat.triggerCondUnknown')
  }
}

/** What a fire does, in a phrase: "sell 50 % of ETH", "buy $50 of ETH". */
function actionPhrase(trigger: Trigger): string {
  const { action, token } = trigger
  const symbol = token.symbol
  if (trigger.kind === 'sell') {
    if (action.amountPct !== null) {
      return t('chat.triggerSellPct', { pct: pctNumber(action.amountPct), token: symbol })
    }
    if (action.amount) {
      return t('chat.triggerSellAmount', {
        amount: formatTokenAmount(action.amount.human),
        token: symbol,
      })
    }
    return t('chat.triggerSellUsd', { usd: formatDcaUsd(action.amountUsd), token: symbol })
  }
  if (trigger.kind === 'buy') {
    return t('chat.triggerBuyUsd', { usd: formatDcaUsd(action.amountUsd), token: symbol })
  }
  return t('chat.triggerActUnknown', { token: symbol })
}

/** The card's sentence: "sell 50 % of ETH when under $3,800". */
export function heroText(trigger: Trigger): string {
  const condition = conditionLabel(trigger)
  if (trigger.kind === 'alert') {
    return t('chat.triggerHeroAlert', { token: trigger.token.symbol, condition })
  }
  return t('chat.triggerHero', { action: actionPhrase(trigger), condition })
}

/**
 * The signed distance to the line in percent of the price: the engine's
 * `distancePct`, else derived from the price and the line. 0 once met; null
 * when either price is unknown.
 */
export function distancePctOf(trigger: Trigger): number | null {
  if (trigger.market.distancePct !== null) return trigger.market.distancePct
  const now = trigger.market.priceUsd
  const c = trigger.condition
  const line = c.direction === 'trail' ? c.stopPriceUsd : c.priceUsd
  if (now === null || line === null) return null
  if (c.direction === 'above') return now >= line ? 0 : ((line - now) / now) * 100
  if (c.direction === 'below' || c.direction === 'trail') {
    return now <= line ? 0 : ((line - now) / now) * 100
  }
  return null
}

/** "0.3 % above the line", "4.0 % below the line", "at the line"; '' when unknown. */
export function distanceText(trigger: Trigger): string {
  const d = distancePctOf(trigger)
  if (d === null) return ''
  const trail = trigger.condition.direction === 'trail'
  if (d === 0) return trail ? t('chat.triggerTrailMet') : t('chat.triggerDistMet')
  const pct = Math.abs(d).toFixed(1)
  if (d < 0) {
    return trail ? t('chat.triggerTrailAbove', { pct }) : t('chat.triggerDistAbove', { pct })
  }
  return t('chat.triggerDistBelow', { pct })
}

/** A signed distance for a list row: "−0.3 %", "+4.0 %", "at the line". */
export function formatDistance(pct: number | null): string {
  if (pct === null || !Number.isFinite(pct)) return ''
  if (pct === 0) return t('chat.triggerDistMet')
  return `${pct > 0 ? '+' : '−'}${Math.abs(pct).toFixed(1)} %`
}

/** "12 s ago", "4 m ago", "3 h ago", "2 d ago" from an ISO stamp; '' when unusable. */
export function agoText(iso: string | null, nowMs: number): string {
  if (!iso) return ''
  const at = Date.parse(iso)
  if (Number.isNaN(at)) return ''
  const seconds = Math.max(0, Math.floor((nowMs - at) / 1000))
  if (seconds < 60) return t('chat.triggerAgoSeconds', { count: String(seconds) })
  if (seconds < 3600)
    return t('chat.triggerAgoMinutes', { count: String(Math.floor(seconds / 60)) })
  if (seconds < 86_400) {
    return t('chat.triggerAgoHours', { count: String(Math.floor(seconds / 3600)) })
  }
  return t('chat.triggerAgoDays', { count: String(Math.floor(seconds / 86_400)) })
}

function msUntil(iso: string | null, nowMs: number): number | null {
  if (!iso) return null
  const at = Date.parse(iso)
  return Number.isNaN(at) ? null : at - nowMs
}

/** "done · sold 0.05 ETH at $3,788" from the order that finished the trigger. */
function doneText(trigger: Trigger): string {
  const { result } = trigger
  if (result && trigger.kind === 'sell' && result.amountIn) {
    return t('chat.triggerNowDoneSold', {
      amount: formatTokenAmount(result.amountIn.human),
      token: trigger.token.symbol,
      price: formatDcaPrice(result.priceUsd),
    })
  }
  if (result && trigger.kind === 'buy' && result.amountOut) {
    return t('chat.triggerNowDoneBought', {
      amount: formatTokenAmount(result.amountOut.human),
      token: trigger.token.symbol,
      price: formatDcaPrice(result.priceUsd),
    })
  }
  const alerted = trigger.fires.find((f) => f.status === 'alerted')
  if (trigger.kind === 'alert' && alerted?.priceUsd) {
    return t('chat.triggerNowDoneAlerted', { price: formatDcaPrice(alerted.priceUsd) })
  }
  return trigger.statusReason
    ? `${t('chat.triggerNowDone')} · ${trigger.statusReason}`
    : t('chat.triggerNowDone')
}

/**
 * The live line under the hero: "ETH $3,790 · 0.3 % above the line · checked
 * 12 s ago", or what the trigger is doing instead. Re-derived by the mounter's
 * clock, so it must stay pure in `(trigger, now)`.
 */
export function nowText(trigger: Trigger, nowMs: number): string {
  switch (trigger.status) {
    case 'awaiting_approval': {
      const left = msUntil(trigger.expiresAt, nowMs)
      const head = t('chat.triggerNowAwaiting')
      if (left === null) return head
      if (left <= 0) return `${head} · ${t('chat.triggerProposalLapsing')}`
      return `${head} · ${t('chat.triggerExpiresIn', { time: formatCountdown(left) })}`
    }
    case 'armed': {
      const ago = agoText(trigger.market.checkedAt, nowMs)
      const checked = ago ? t('chat.triggerChecked', { ago }) : ''
      const now = trigger.market.priceUsd
      if (now === null) {
        return [t('chat.triggerWaitingPrice'), checked].filter(Boolean).join(' · ')
      }
      const { hits, confirmTicks } = trigger.condition
      const parts = [`${trigger.token.symbol} ${formatDcaPrice(now)}`]
      if (hits > 0 && hits < confirmTicks) {
        parts.push(tPlural('chat.triggerConfirming', confirmTicks - hits))
      } else {
        parts.push(distanceText(trigger))
      }
      parts.push(checked)
      return parts.filter(Boolean).join(' · ')
    }
    case 'triggered':
      return t('chat.triggerNowTriggered')
    case 'paused':
      return t('chat.triggerNowPaused')
    case 'done':
      return doneText(trigger)
    case 'stopped':
      return t('chat.triggerNowStopped')
    case 'rejected':
      return t('chat.triggerNowRejected')
    case 'expired':
      return t('chat.triggerNowExpired')
    default:
      return ''
  }
}

/**
 * When the clock should next wake for `trigger`'s live line: every second
 * while "checked N s ago" counts seconds or a proposal's expiry is under an
 * hour away, on the next minute boundary of the stamp above that, once a
 * minute when nothing ticks.
 */
export function triggerTickDelay(trigger: Trigger, nowMs: number): number {
  if (trigger.status === 'awaiting_approval') {
    return dcaTickDelay(msUntil(trigger.expiresAt, nowMs))
  }
  if (trigger.status !== 'armed' || !trigger.market.checkedAt) return DCA_MINUTE_MS
  const at = Date.parse(trigger.market.checkedAt)
  if (Number.isNaN(at)) return DCA_MINUTE_MS
  const age = Math.max(0, nowMs - at)
  if (age < DCA_MINUTE_MS) return DCA_SECOND_MS
  if (age < HOUR_MS) return Math.max(DCA_SECOND_MS, DCA_MINUTE_MS - (age % DCA_MINUTE_MS))
  return DCA_MINUTE_MS
}

/** The controls a trigger in `status` offers, primary first. Terminal → none. */
export function triggerActionsFor(status: TriggerStatus): TriggerAction[] {
  switch (status) {
    case 'awaiting_approval':
      return ['approve', 'reject']
    case 'armed':
      return ['pause', 'fire', 'stop']
    case 'paused':
      return ['resume', 'fire', 'stop']
    case 'triggered':
      return ['stop']
    default:
      return []
  }
}

const ACTIONS = new Set<TriggerAction>(['approve', 'reject', 'pause', 'resume', 'fire', 'stop'])

/** Actions that take a confirming second click. */
const CONFIRMED = new Set<TriggerAction>(['fire', 'stop'])

export function triggerActionLabel(action: TriggerAction, kind: TriggerActionKind): string {
  switch (action) {
    case 'approve':
      return t('chat.triggerApprove')
    case 'reject':
      return t('chat.triggerReject')
    case 'pause':
      return t('chat.triggerPause')
    case 'resume':
      return t('chat.triggerResume')
    case 'fire':
      return kind === 'sell'
        ? t('chat.triggerSellNow')
        : kind === 'buy'
          ? t('chat.triggerBuyNow')
          : t('chat.triggerFire')
    case 'stop':
      return t('chat.triggerStop')
  }
}

function actionTitle(action: TriggerAction): string {
  switch (action) {
    case 'approve':
      return t('chat.triggerApproveTitle')
    case 'reject':
      return t('chat.triggerRejectTitle')
    case 'pause':
      return t('chat.triggerPauseTitle')
    case 'resume':
      return t('chat.triggerResumeTitle')
    case 'fire':
      return t('chat.triggerFireTitle')
    case 'stop':
      return t('chat.triggerStopTitle')
  }
}

export function triggerStatusLabel(status: TriggerStatus): string {
  switch (status) {
    case 'awaiting_approval':
      return t('chat.triggerStatusAwaiting')
    case 'armed':
      return t('chat.triggerStatusArmed')
    case 'triggered':
      return t('chat.triggerStatusTriggered')
    case 'paused':
      return t('chat.triggerStatusPaused')
    case 'done':
      return t('chat.triggerStatusDone')
    case 'stopped':
      return t('chat.triggerStatusStopped')
    case 'rejected':
      return t('chat.triggerStatusRejected')
    case 'expired':
      return t('chat.triggerStatusExpired')
    default:
      return t('chat.triggerStatusUnknown')
  }
}

function fireStatusLabel(status: TriggerFireStatus): string {
  switch (status) {
    case 'pending':
      return t('chat.triggerFirePending')
    case 'filled':
      return t('chat.triggerFireFilled')
    case 'parked':
      return t('chat.triggerFireParked')
    case 'alerted':
      return t('chat.triggerFireAlerted')
    case 'skipped':
      return t('chat.triggerFireSkipped')
    case 'failed':
      return t('chat.triggerFireFailed')
    case 'expired':
      return t('chat.triggerFireExpired')
    case 'rejected':
      return t('chat.triggerFireRejected')
    default:
      return t('chat.triggerFireUnknown')
  }
}

/** Words for a reason code, when the engine sent no human reason. */
function reasonCodeText(code: string | null): string {
  switch (code) {
    case 'insufficient_balance':
      return t('chat.triggerReasonBalance')
    case 'trading.interrupted':
      return t('chat.triggerReasonInterrupted')
    default:
      return code ?? ''
  }
}

/** What a fire did, in one phrase (the part after its status word). */
export function fireDetail(fire: TriggerFire, trigger: Trigger): string {
  const at =
    fire.priceUsd !== null ? t('chat.triggerFireAt', { price: formatDcaPrice(fire.priceUsd) }) : ''
  switch (fire.status) {
    case 'filled': {
      const result = trigger.result
      if (result && fire.orderId && result.orderId === fire.orderId && result.amountIn) {
        const sell = trigger.kind !== 'buy'
        return t('chat.triggerFireSwap', {
          amountIn: formatTokenAmount(result.amountIn.human),
          symbolIn: sell ? trigger.token.symbol : trigger.quote.symbol,
          amountOut: result.amountOut ? formatTokenAmount(result.amountOut.human) : NO_VALUE,
          symbolOut: sell ? trigger.quote.symbol : trigger.token.symbol,
          price: formatDcaPrice(result.priceUsd ?? fire.priceUsd),
        })
      }
      return at
    }
    case 'skipped':
    case 'failed':
      return fire.reason ?? (reasonCodeText(fire.reasonCode) || at)
    case 'parked':
    case 'pending':
    case 'alerted':
      return at
    default:
      return fire.reason ?? at
  }
}

/** A local date + time for a tooltip or a fact; '' when the stamp is unusable. */
function formatWhen(iso: string, withTime: boolean): string {
  const at = Date.parse(iso)
  if (!iso || Number.isNaN(at)) return ''
  const options: Intl.DateTimeFormatOptions = withTime
    ? { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false }
    : { month: 'short', day: 'numeric', year: 'numeric' }
  return new Date(at).toLocaleString('en-US', options)
}

/* ── Pure helpers: the gauge ────────────────────────────────────────────── */

export type TriggerProximity = 'met' | 'near' | 'far'

export interface TriggerGauge {
  /** The price domain the rail spans (left, right). */
  lo: number
  hi: number
  /** Positions 0–1 along the rail; null when that price is unknown or not drawn. */
  current: number | null
  /** The below/above line. */
  trigger: number | null
  /** trail: the highest price since arming. */
  peak: number | null
  /** trail: the price it would fire at now. */
  stop: number | null
  /** The span of the rail where the condition holds, 0–1, or null. */
  zone: [number, number] | null
  distancePct: number | null
  proximity: TriggerProximity | null
}

/** "met" at 0, "near" within TRIGGER_NEAR_PCT, else "far"; null when unknown. */
export function proximityOf(distancePct: number | null): TriggerProximity | null {
  if (distancePct === null || !Number.isFinite(distancePct)) return null
  if (distancePct === 0) return 'met'
  return Math.abs(distancePct) <= TRIGGER_NEAR_PCT ? 'near' : 'far'
}

/**
 * Lay the prices out on one rail: the current price, the line (below/above)
 * or the peak and the stop (trail), on an axis padded past the data (by at
 * least TRIGGER_GAUGE_MIN_PAD of the line) so the marks never sit on the
 * ends. Null when there is no line to draw.
 */
export function triggerGauge(trigger: Trigger): TriggerGauge | null {
  const c = trigger.condition
  const trail = c.direction === 'trail'
  if (c.direction === 'unknown') return null
  const line = trail ? c.stopPriceUsd : c.priceUsd
  if (line === null) return null
  const now = trigger.market.priceUsd
  const peak = trail ? c.peakPriceUsd : null
  const known = [now, line, peak].filter((p): p is number => p !== null)
  const min = Math.min(...known)
  const max = Math.max(...known)
  const pad = Math.max((max - min) * 0.25, line * TRIGGER_GAUGE_MIN_PAD)
  const lo = Math.max(0, min - pad)
  const hi = max + pad
  const at = (p: number | null): number | null =>
    p === null || !(hi > lo) ? null : clamp01((p - lo) / (hi - lo))
  const linePos = at(line) as number
  const distancePct = distancePctOf(trigger)
  return {
    lo,
    hi,
    current: at(now),
    trigger: trail ? null : linePos,
    peak: at(peak),
    stop: trail ? linePos : null,
    zone: c.direction === 'above' ? [linePos, 1] : [0, linePos],
    distancePct,
    proximity: proximityOf(distancePct),
  }
}

/* ── Pure helpers: layout ───────────────────────────────────────────────── */

export type TriggerLayout = 'wide' | 'narrow'

export function triggerLayoutFor(cardPx: number): TriggerLayout {
  return cardPx >= TRIGGER_WIDE_MIN_PX ? 'wide' : 'narrow'
}

/* ── DOM building ───────────────────────────────────────────────────────── */

const SVG_NS = 'http://www.w3.org/2000/svg'

function el(tag: string, className: string, content?: string): HTMLElement {
  const node = document.createElement(tag)
  if (className) node.className = className
  // textContent, never innerHTML — see the security note at the top.
  if (content !== undefined) node.textContent = content
  return node
}

function svg(tag: string, attrs: Record<string, string | number>): SVGElement {
  const node = document.createElementNS(SVG_NS, tag)
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value))
  return node
}

function hidden(node: HTMLElement): HTMLElement {
  node.setAttribute('aria-hidden', 'true')
  return node
}

/** Everything the DOM builders need from the mounter. */
export interface TriggerRenderContext {
  now: () => number
  copyText: (value: string) => void | Promise<void>
  /** A cancellable timer the mounter clears on unmount. */
  setTimer: (fn: () => void, ms: number) => void
  /** Offer ↻ on a card whose payload says how to re-run it. */
  readonly canRefresh?: boolean
  /**
   * Offer the trigger controls (approve, pause, fire now, stop…). Only an
   * operator connection (the desktop desk) can use them; the web never sets it.
   */
  readonly canWrite?: boolean
}

function statusPill(status: TriggerStatus): HTMLElement {
  const pill = el('span', 'trigger-pill')
  pill.dataset.status = status
  pill.append(
    hidden(el('span', 'trigger-pill__dot')),
    el('span', 'trigger-pill__text', triggerStatusLabel(status)),
  )
  return pill
}

function actionButton(
  action: TriggerAction,
  trigger: Trigger,
  compact: boolean,
): HTMLButtonElement {
  const button = el(
    'button',
    'trigger-action',
    triggerActionLabel(action, trigger.kind),
  ) as HTMLButtonElement
  button.type = 'button'
  button.dataset.triggerOp = action
  button.dataset.triggerId = trigger.id
  button.title = actionTitle(action)
  if (action === 'approve') button.dataset.triggerTone = 'primary'
  else if (action === 'stop' || action === 'reject') button.dataset.triggerTone = 'danger'
  if (compact) button.dataset.triggerCompact = 'true'
  return button
}

/**
 * The controls row for one trigger, keyed by `data-trigger-id`. The mounter's
 * click handler calls the RPC and drives `data-trigger-busy` and the error
 * line. Null for a trigger with nothing left to control.
 */
function actionsRow(trigger: Trigger, compact: boolean): HTMLElement | null {
  const actions = triggerActionsFor(trigger.status)
  if (actions.length === 0) return null
  const row = el('div', compact ? 'trigger-actions trigger-actions--compact' : 'trigger-actions')
  row.dataset.triggerId = trigger.id
  row.setAttribute('role', 'group')
  row.setAttribute('aria-label', t('chat.triggerActionsLabel', { name: trigger.name }))
  const buttons = el('div', 'trigger-actions__buttons')
  actions.forEach((action) => buttons.append(actionButton(action, trigger, compact)))
  const error = el('p', 'trigger-actions__error')
  error.setAttribute('role', 'alert')
  error.hidden = true
  row.append(buttons, error)
  return row
}

function nowNode(trigger: Trigger, nowMs: number): HTMLElement {
  const node = el('p', 'trigger-card__now', nowText(trigger, nowMs))
  node.setAttribute('aria-live', 'off')
  if (trigger.condition.hits > 0) {
    node.dataset.triggerHits = `${trigger.condition.hits}/${trigger.condition.confirmTicks}`
  }
  return node
}

/** "line set at −10 % from $3,780" when the user gave a percent at creation. */
function fromNode(trigger: Trigger): HTMLElement | null {
  const { fromPriceUsd, priceUsd } = trigger.condition
  if (fromPriceUsd === null || priceUsd === null) return null
  const move = ((priceUsd - fromPriceUsd) / fromPriceUsd) * 100
  return el(
    'p',
    'trigger-card__from',
    t('chat.triggerFromPrice', {
      pct: `${move > 0 ? '+' : move < 0 ? '−' : ''}${Math.abs(move).toFixed(1)} %`,
      price: formatDcaPrice(fromPriceUsd),
    }),
  )
}

/* ── the gauge ── */

function labelAlign(x: number): 'start' | 'center' | 'end' {
  return x < 0.15 ? 'start' : x > 0.85 ? 'end' : 'center'
}

function pct(x: number): string {
  return `${(x * 100).toFixed(2)}%`
}

function gaugeNode(trigger: Trigger): HTMLElement | null {
  const model = triggerGauge(trigger)
  if (!model) return null
  const c = trigger.condition
  const trail = c.direction === 'trail'
  const linePrice = trail ? c.stopPriceUsd : c.priceUsd
  const gauge = el('div', 'trigger-gauge')
  gauge.dataset.triggerDirection = c.direction
  // A finished trigger has no distance left to tint: the dot is just today's price.
  if (!isTriggerTerminal(trigger.status)) {
    if (model.distancePct !== null) {
      gauge.dataset.triggerDist = String(Math.round(model.distancePct * 100) / 100)
    }
    if (model.proximity) gauge.dataset.triggerProximity = model.proximity
  }
  gauge.setAttribute('role', 'img')
  gauge.setAttribute(
    'aria-label',
    trail
      ? t('chat.triggerGaugeTrailLabel', {
          now: formatDcaPrice(trigger.market.priceUsd),
          peak: formatDcaPrice(c.peakPriceUsd),
          stop: formatDcaPrice(c.stopPriceUsd),
        })
      : t('chat.triggerGaugeLabel', {
          now: formatDcaPrice(trigger.market.priceUsd),
          line: formatDcaPrice(c.priceUsd),
        }),
  )

  // Percent coordinates without a viewBox: the dot stays round at any width.
  const chart = svg('svg', {
    class: 'trigger-gauge__svg',
    'aria-hidden': 'true',
    focusable: 'false',
  })
  if (model.zone) {
    chart.append(
      svg('rect', {
        class: 'trigger-gauge__zone',
        x: pct(model.zone[0]),
        width: pct(Math.max(0, model.zone[1] - model.zone[0])),
        y: '30%',
        height: '40%',
      }),
    )
  }
  chart.append(
    svg('line', { class: 'trigger-gauge__rail', x1: '0%', x2: '100%', y1: '50%', y2: '50%' }),
  )
  const lineAt = trail ? model.stop : model.trigger
  if (lineAt !== null) {
    chart.append(
      svg('line', {
        class: 'trigger-gauge__line',
        'data-mark': trail ? 'stop' : 'line',
        x1: pct(lineAt),
        x2: pct(lineAt),
        y1: '10%',
        y2: '90%',
      }),
    )
  }
  if (model.peak !== null) {
    chart.append(
      svg('line', {
        class: 'trigger-gauge__peak',
        'data-mark': 'peak',
        x1: pct(model.peak),
        x2: pct(model.peak),
        y1: '25%',
        y2: '75%',
      }),
    )
  }
  if (model.current !== null) {
    chart.append(
      svg('circle', {
        class: 'trigger-gauge__now',
        'data-mark': 'now',
        cx: pct(model.current),
        cy: '50%',
        r: 5,
      }),
    )
  }
  gauge.append(chart)

  // Labels: the line (and peak) above the rail, the current price under it,
  // so a price sitting on the line never draws over its label.
  const label = (mark: string, x: number, content: string): HTMLElement => {
    const node = el('span', 'trigger-gauge__label', content)
    node.dataset.mark = mark
    node.dataset.align = labelAlign(x)
    node.style.left = pct(x)
    return node
  }
  const top = hidden(el('div', 'trigger-gauge__labels'))
  top.dataset.row = 'top'
  if (lineAt !== null) {
    top.append(
      label(
        trail ? 'stop' : 'line',
        lineAt,
        trail
          ? t('chat.triggerGaugeStop', { price: formatDcaPrice(linePrice) })
          : t('chat.triggerGaugeLine', { price: formatDcaPrice(linePrice) }),
      ),
    )
  }
  if (model.peak !== null) {
    top.append(
      label(
        'peak',
        model.peak,
        t('chat.triggerGaugePeak', { price: formatDcaPrice(c.peakPriceUsd) }),
      ),
    )
  }
  const bottom = hidden(el('div', 'trigger-gauge__labels'))
  bottom.dataset.row = 'bottom'
  if (model.current !== null) {
    bottom.append(
      label(
        'now',
        model.current,
        t('chat.triggerGaugeNow', { price: formatDcaPrice(trigger.market.priceUsd) }),
      ),
    )
  }
  gauge.prepend(top)
  gauge.append(bottom)
  return gauge
}

/* ── facts ── */

function fact(key: string, label: string, value: string, noValue = false): HTMLElement {
  const cell = el('div', 'trigger-fact')
  cell.dataset.triggerFact = key
  const node = el('span', 'trigger-fact__value', value)
  if (noValue) {
    node.dataset.triggerNoValue = 'true'
    node.title = t('chat.triggerNoPriceTitle')
  }
  cell.append(el('span', 'trigger-fact__label', label), node)
  return cell
}

/** A trigger that will not watch the price again: done, stopped, rejected, expired. */
export function isTriggerTerminal(status: TriggerStatus): boolean {
  return status === 'done' || status === 'stopped' || status === 'rejected' || status === 'expired'
}

/**
 * What the fill moved: "0.02 WETH → 42.4 USDC · ≈ $42.4" (amountIn alone
 * when the out side is unknown); '' when there is no result to read.
 */
function movedText(trigger: Trigger): string {
  const result = trigger.result
  if (!result?.amountIn?.human) return ''
  const sell = trigger.kind !== 'buy'
  const symbolIn = sell ? trigger.token.symbol : trigger.quote.symbol
  const symbolOut = sell ? trigger.quote.symbol : trigger.token.symbol
  const amountIn = formatTokenAmount(result.amountIn.human)
  const moved = result.amountOut?.human
    ? t('chat.triggerSizeMoved', {
        amountIn,
        symbolIn,
        amountOut: formatTokenAmount(result.amountOut.human),
        symbolOut,
      })
    : `${amountIn} ${symbolIn}`
  const usd = result.amountIn.usd ?? result.amountOut?.usd ?? null
  return [moved, usd !== null ? t('chat.triggerApprox', { usd: formatDcaUsd(usd) }) : '']
    .filter(Boolean)
    .join(' · ')
}

/**
 * "50 % · ≈ $189", "$50", "0.05 ETH · ≈ $189", "notify only", "—"; once a
 * terminal trigger has filled, what the fill moved instead of what a fire
 * would move now.
 */
export function sizeText(trigger: Trigger): string {
  const { action } = trigger
  if (trigger.kind === 'alert') return t('chat.triggerSizeNotify')
  if (isTriggerTerminal(trigger.status)) {
    const moved = movedText(trigger)
    if (moved) return moved
  }
  const approx =
    action.estimatedUsd !== null
      ? t('chat.triggerApprox', { usd: formatDcaUsd(action.estimatedUsd) })
      : ''
  if (trigger.kind === 'sell') {
    if (action.amountPct !== null) {
      return [`${pctNumber(action.amountPct)} %`, approx].filter(Boolean).join(' · ')
    }
    if (action.amount) {
      return [`${formatTokenAmount(action.amount.human)} ${trigger.token.symbol}`, approx]
        .filter(Boolean)
        .join(' · ')
    }
    return formatDcaUsd(action.amountUsd)
  }
  if (trigger.kind === 'buy') return formatDcaUsd(action.amountUsd)
  return NO_VALUE
}

/**
 * size · wallet balance · valid until · approval. The balance is the wallet
 * now, so a finished trigger (or an alert, which spends nothing) leaves it
 * out rather than show a figure that is not part of its story; an alert
 * places no order, so it has no approval either. An odd count lets the size
 * span the row (CSS).
 */
function factsSection(trigger: Trigger): HTMLElement {
  const facts = el('section', 'trigger-card__facts')
  const terminal = isTriggerTerminal(trigger.status)
  const size = sizeText(trigger)
  const sizeCell = fact('size', t('chat.triggerFactSize'), size, size === NO_VALUE)
  if (size !== NO_VALUE) sizeCell.title = size
  facts.append(sizeCell)

  if (trigger.kind !== 'alert' && !terminal) {
    const balance = trigger.market.balance
    const balanceSymbol = trigger.kind === 'buy' ? trigger.quote.symbol : trigger.token.symbol
    facts.append(
      fact(
        'balance',
        t('chat.triggerFactBalance'),
        balance && balance.human
          ? `${formatTokenAmount(balance.human)} ${balanceSymbol}`
          : NO_VALUE,
        !balance,
      ),
    )
  }

  const valid = trigger.validUntil ? formatWhen(trigger.validUntil, false) : ''
  const validCell = fact('valid', t('chat.triggerFactValid'), valid || t('chat.triggerGtc'))
  if (trigger.validUntil) validCell.title = formatWhen(trigger.validUntil, true)
  facts.append(validCell)

  if (trigger.kind !== 'alert') {
    const approval = trigger.action.needsApproval
      ? t('chat.triggerApprovalWaits', {
          usd: formatDcaUsd(trigger.action.approvalThresholdUsd),
        })
      : t('chat.triggerApprovalAuto')
    const approvalCell = fact('approval', t('chat.triggerFactApproval'), approval)
    // Only a trigger that can still fire has anything to wait for.
    if (trigger.action.needsApproval && !terminal) approvalCell.dataset.triggerWaits = 'true'
    facts.append(approvalCell)
  }
  return facts
}

/* ── fires ── */

function explorerHost(url: string): string {
  try {
    return new URL(url).host
  } catch {
    return url
  }
}

function fireRow(fire: TriggerFire, trigger: Trigger, nowMs: number): HTMLElement {
  const row = el('li', 'trigger-fire')
  row.dataset.triggerFireStatus = fire.status
  if (fire.reasonCode) row.dataset.triggerReason = fire.reasonCode
  row.append(el('span', 'trigger-fire__n', `#${fire.n}`))
  const ago = relativeTime(fire.at, nowMs)
  const time = el('time', 'trigger-fire__ago', ago || NO_VALUE)
  if (ago) {
    time.setAttribute('datetime', fire.at)
    time.dataset.triggerAt = fire.at
    time.title = formatWhen(fire.at, true)
  }
  row.append(time, el('span', 'trigger-fire__status', fireStatusLabel(fire.status)))
  const detail = fireDetail(fire, trigger)
  if (detail) row.append(el('span', 'trigger-fire__detail', detail))
  if (fire.manual) row.append(el('span', 'trigger-fire__manual', t('chat.triggerFireManual')))
  if (fire.explorerUrl) {
    const link = el('a', 'trigger-fire__link', '↗') as HTMLAnchorElement
    link.href = fire.explorerUrl
    link.target = '_blank'
    link.rel = 'noopener noreferrer'
    const host = explorerHost(fire.explorerUrl)
    link.title = t('chat.triggerExplorerTitle', { explorer: host })
    link.setAttribute('aria-label', t('chat.triggerExplorerTitle', { explorer: host }))
    row.append(link)
  }
  return row
}

function firesSection(trigger: Trigger, nowMs: number): HTMLElement | null {
  if (trigger.fires.length === 0) return null
  const section = el('section', 'trigger-fires')
  section.append(el('h4', 'trigger-fires__title', t('chat.triggerFiresTitle')))
  const listNode = el('ol', 'trigger-fires__list')
  trigger.fires
    .slice(0, TRIGGER_RECENT_FIRES)
    .forEach((fire) => listNode.append(fireRow(fire, trigger, nowMs)))
  section.append(listNode)
  return section
}

/* ── footer ── */

function copyButton(id: string, ctx: TriggerRenderContext): HTMLElement {
  const button = el('button', 'trigger-card__action trigger-card__copy') as HTMLButtonElement
  button.type = 'button'
  button.dataset.triggerFoot = 'copy'
  button.title = t('chat.triggerCopyTitle', { id })
  button.setAttribute('aria-label', t('chat.triggerCopyTitle', { id }))
  const glyph = hidden(el('span', 'trigger-card__action-glyph', '⧉'))
  const label = el('span', 'trigger-card__action-label', t('chat.triggerCopyId'))
  button.append(glyph, label)
  let generation = 0
  const settle = (ok: boolean): void => {
    const mine = ++generation
    button.dataset.triggerCopied = ok ? 'true' : 'failed'
    glyph.textContent = ok ? '✓' : '✕'
    label.textContent = ok ? t('chat.triggerCopied') : t('chat.triggerCopyFailed')
    ctx.setTimer(() => {
      if (mine !== generation) return
      delete button.dataset.triggerCopied
      glyph.textContent = '⧉'
      label.textContent = t('chat.triggerCopyId')
    }, TRIGGER_COPIED_MS)
  }
  button.addEventListener('click', () => {
    void Promise.resolve()
      .then(() => ctx.copyText(id))
      .then(
        () => settle(true),
        () => settle(false),
      )
  })
  return button
}

function refreshButton(): HTMLElement {
  const button = el('button', 'trigger-card__action trigger-card__refresh') as HTMLButtonElement
  button.type = 'button'
  button.dataset.triggerFoot = 'refresh'
  button.title = t('chat.triggerRefreshTitle')
  button.setAttribute('aria-label', t('chat.triggerRefreshTitle'))
  button.append(
    hidden(el('span', 'trigger-card__action-glyph', '↻')),
    el('span', 'trigger-card__action-label', t('chat.triggerRefresh')),
  )
  return button
}

/**
 * Why a card's controls are off: its live read is in flight (`checking`) or
 * failed (`failed`). Stamped as `data-trigger-stale` on the `.trigger-card`.
 */
export type TriggerStale = 'checking' | 'failed'

/** "state may be stale · ↻" — shown while a card's live read has failed. */
function staleHint(): HTMLElement {
  const hint = el('p', 'trigger-card__stale')
  hint.setAttribute('role', 'status')
  hint.title = t('chat.triggerStaleTitle')
  const button = el('button', 'trigger-card__stale-refresh', '↻') as HTMLButtonElement
  button.type = 'button'
  button.dataset.triggerFoot = 'refresh'
  button.title = t('chat.triggerRefreshTitle')
  button.setAttribute('aria-label', t('chat.triggerRefreshTitle'))
  hint.append(
    el('span', 'trigger-card__stale-text', t('chat.triggerStale')),
    hidden(el('span', 'trigger-sep', ' · ')),
    button,
  )
  return hint
}

function walletText(wallet: LpWallet | null): string {
  if (!wallet) return ''
  const short = shortAddress(wallet.address)
  return wallet.label ? `${wallet.label} (${short})` : short
}

function footer(
  payload: TriggerPayload,
  parts: string[],
  copyId: string,
  ctx: TriggerRenderContext,
): HTMLElement {
  const foot = el('footer', 'trigger-card__foot')
  const meta = el('span', 'trigger-card__foot-meta')
  meta.textContent = parts.filter(Boolean).join(' · ')
  const ago = relativeTime(payload.fetchedAt, ctx.now())
  if (ago) {
    if (meta.textContent) meta.append(el('span', 'trigger-sep', ' · '))
    meta.append(el('span', 'trigger-card__as-of', `${t('chat.triggerAsOf')} `))
    const time = el('time', 'trigger-card__ago', ago)
    time.setAttribute('datetime', payload.fetchedAt)
    time.dataset.triggerFetchedAt = payload.fetchedAt
    time.title = payload.fetchedAt
    meta.append(time)
  }
  foot.append(meta)
  const actions = el('span', 'trigger-card__actions')
  if (ctx.canRefresh && payload.request) actions.append(refreshButton())
  if (copyId) actions.append(copyButton(copyId, ctx))
  if (actions.childElementCount) foot.append(actions)
  return foot
}

function warningsNode(warnings: string[]): HTMLElement | null {
  if (warnings.length === 0) return null
  const listNode = el('ul', 'trigger-card__warnings')
  warnings.forEach((w) => listNode.append(el('li', 'trigger-card__warning', w)))
  return listNode
}

function shell(kind: TriggerKind): HTMLElement {
  const card = el('article', 'trigger-card')
  card.dataset.triggerKind = kind
  // The unmeasured default; the mounter re-stamps it from the card's width.
  card.dataset.triggerLayout = 'narrow'
  return card
}

/* ── kind = trigger ── */

function buildOne(payload: TriggerOnePayload, ctx: TriggerRenderContext): HTMLElement {
  const { trigger } = payload
  const nowMs = ctx.now()
  const card = shell('trigger')
  card.dataset.triggerId = trigger.id
  card.dataset.triggerAction = trigger.kind
  card.dataset.triggerStatus = trigger.status
  card.dataset.triggerDirection = trigger.condition.direction
  if (trigger.chain?.key) card.dataset.triggerChain = trigger.chain.key

  const head = el('header', 'trigger-card__head')
  const title = el('div', 'trigger-card__title')
  // The kind glyph (▼ sell, ▲ buy, bell for alert) is drawn by CSS from
  // `data-trigger-action`; the DOM carries no emoji.
  title.append(hidden(el('span', 'trigger-card__glyph')))
  const name = el('span', 'trigger-card__name', trigger.name)
  name.title = trigger.name
  title.append(name)
  if (trigger.chain) title.append(el('span', 'trigger-chain', trigger.chain.name))
  head.append(title, statusPill(trigger.status))
  card.append(head)

  const hero = el('section', 'trigger-card__hero')
  const line = el('p', 'trigger-card__hero-line', heroText(trigger))
  if (trigger.action.label) line.title = trigger.action.label
  hero.append(line, nowNode(trigger, nowMs))
  const from = fromNode(trigger)
  if (from) hero.append(from)
  if (trigger.statusReason && trigger.status !== 'done') {
    hero.append(el('p', 'trigger-card__reason', statusReasonText(trigger.statusReason)))
  }
  card.append(hero)

  const gauge = gaugeNode(trigger)
  if (gauge) card.append(gauge)
  card.append(factsSection(trigger))
  const fires = firesSection(trigger, nowMs)
  if (fires) card.append(fires)
  if (ctx.canWrite) {
    const actions = actionsRow(trigger, false)
    if (actions) card.append(actions)
  }
  const warnings = warningsNode(payload.warnings)
  if (warnings) card.append(warnings)
  card.append(footer(payload, [trigger.id, walletText(trigger.wallet)], trigger.id, ctx))
  return card
}

/* ── kind = triggers ── */

/** "sell 50 % ETH · under $3,800" for a list row. */
export function rowPlanText(trigger: Trigger): string {
  const { action, token } = trigger
  const symbol = token.symbol
  let what: string
  if (trigger.kind === 'sell') {
    what =
      action.amountPct !== null
        ? t('chat.triggerRowSellPct', { pct: pctNumber(action.amountPct), token: symbol })
        : action.amount
          ? t('chat.triggerRowSellAmount', {
              amount: formatTokenAmount(action.amount.human),
              token: symbol,
            })
          : t('chat.triggerRowSellUsd', { usd: formatDcaUsd(action.amountUsd), token: symbol })
  } else if (trigger.kind === 'buy') {
    what = t('chat.triggerRowBuy', { usd: formatDcaUsd(action.amountUsd), token: symbol })
  } else {
    what = t('chat.triggerRowAlert', { token: symbol })
  }
  return `${what} · ${conditionLabel(trigger)}`
}

/** "ETH $3,790 · −0.3 %" for a list row; the state word stands in when there is no price. */
export function rowNowText(trigger: Trigger): string {
  const now = trigger.market.priceUsd
  const parts: string[] = []
  if (now !== null) parts.push(`${trigger.token.symbol} ${formatDcaPrice(now)}`)
  if (trigger.status === 'armed' || trigger.status === 'paused') {
    const { hits, confirmTicks } = trigger.condition
    if (trigger.status === 'armed' && hits > 0 && hits < confirmTicks) {
      parts.push(tPlural('chat.triggerConfirming', confirmTicks - hits))
    } else {
      parts.push(formatDistance(distancePctOf(trigger)))
    }
  } else if (trigger.status === 'triggered') {
    parts.push(t('chat.triggerNowTriggered'))
  }
  return parts.filter(Boolean).join(' · ')
}

function triggerRow(trigger: Trigger, ctx: TriggerRenderContext): HTMLElement {
  const row = el('li', 'trigger-row')
  row.dataset.triggerId = trigger.id
  row.dataset.triggerStatus = trigger.status
  row.dataset.triggerAction = trigger.kind
  if (trigger.chain?.key) row.dataset.triggerChain = trigger.chain.key
  const proximity = proximityOf(distancePctOf(trigger))
  if (proximity && trigger.status === 'armed') row.dataset.triggerProximity = proximity
  const dot = hidden(el('span', 'trigger-row__dot'))
  const main = el('div', 'trigger-row__main')
  const top = el('div', 'trigger-row__top')
  const name = el('span', 'trigger-row__name', trigger.name)
  name.title = trigger.name
  top.append(name, el('span', 'trigger-row__state', triggerStatusLabel(trigger.status)))
  main.append(top, el('span', 'trigger-row__plan', rowPlanText(trigger)))
  const now = rowNowText(trigger)
  if (now) main.append(el('span', 'trigger-row__now', now))
  row.append(dot, main)
  if (ctx.canWrite) {
    const actions = actionsRow(trigger, true)
    if (actions) row.append(actions)
  }
  return row
}

function totalsText(totals: TriggerListPayload['totals']): string {
  const parts: string[] = []
  if (totals.armed > 0) parts.push(t('chat.triggerTotalsArmed', { count: String(totals.armed) }))
  if (totals.awaiting > 0) {
    parts.push(t('chat.triggerTotalsAwaiting', { count: String(totals.awaiting) }))
  }
  if (totals.triggered > 0) {
    parts.push(t('chat.triggerTotalsTriggered', { count: String(totals.triggered) }))
  }
  return parts.join(' · ')
}

function buildList(payload: TriggerListPayload, ctx: TriggerRenderContext): HTMLElement {
  const card = shell('triggers')
  const head = el('header', 'trigger-card__head')
  const title = el('div', 'trigger-card__title')
  title.append(
    el('span', 'trigger-card__list-title', t('chat.triggerListTitle')),
    hidden(el('span', 'trigger-sep', ' · ')),
    el('span', 'trigger-card__count', String(payload.totals.count)),
  )
  head.append(title)
  card.append(head)

  if (payload.triggers.length === 0) {
    card.append(el('p', 'trigger-card__empty', t('chat.triggerEmpty')))
  } else {
    const totals = totalsText(payload.totals)
    if (totals) card.append(el('p', 'trigger-totals', totals))
    const rows = el('ul', 'trigger-rows')
    payload.triggers.forEach((tr) => rows.append(triggerRow(tr, ctx)))
    card.append(rows)
  }
  const warnings = warningsNode(payload.warnings)
  if (warnings) card.append(warnings)
  card.append(footer(payload, [], '', ctx))
  return card
}

/**
 * Build the card for a normalized payload. Exported for the unit tests, which
 * assert on the built DOM without standing up a mounter or a fetch.
 */
export function buildTriggerCard(payload: TriggerPayload, ctx: TriggerRenderContext): HTMLElement {
  return payload.kind === 'trigger' ? buildOne(payload, ctx) : buildList(payload, ctx)
}

/** Replace `host`'s card slot with the payload's card. */
export function renderTrigger(
  host: HTMLElement,
  payload: TriggerPayload,
  ctx: TriggerRenderContext,
): void {
  const slot = host.querySelector<HTMLElement>('.msg-artifact-trigger__body')
  if (!slot) return
  slot.replaceChildren(buildTriggerCard(payload, ctx))
  host.dataset.triggerHost = 'rendered'
}

/** Stamp `data-trigger-layout` from the card's measured width; an unmeasured card keeps its default. */
export function layoutTriggerCard(card: HTMLElement): void {
  const width = card.offsetWidth
  if (!(width > 0)) return
  const layout = triggerLayoutFor(width)
  if (card.dataset.triggerLayout !== layout) card.dataset.triggerLayout = layout
}

/* ── Mounter ────────────────────────────────────────────────────────────── */

/** A gateway RPC call: `rpc.call(method, params)`. */
export type TriggerCall = (method: string, params: Record<string, unknown>) => Promise<unknown>

/**
 * The write capability the desktop desk hands the mounter for its operator
 * connection; the same shape as `DcaActions`. Absent (the web) → no controls.
 */
export interface TriggerActions {
  /** Calls `trading.trigger.<action>` on the operator connection. */
  call: TriggerCall
  /** A "Fire now" placed an order (the desk focuses it in the Book). */
  onOrder?: (orderId: string) => void
}

/** The read RPC a request re-runs. */
export function triggerReadMethod(request: TriggerRequest): string {
  return `trading.trigger.${request.kind}`
}

function rpcError(error: unknown): { code: string; message: string } {
  const e = error as { code?: unknown; message?: unknown } | null
  return {
    code: typeof e?.code === 'string' ? e.code : '',
    message: typeof e?.message === 'string' ? e.message : String(error),
  }
}

/** What a failed control says in `.trigger-actions__error`. */
export function triggerErrorText(error: unknown): string {
  const { code, message } = rpcError(error)
  if (code === 'trading.operator_required') return t('chat.triggerErrOperator')
  return message || code || t('chat.triggerErrUnknown')
}

/**
 * A `trading.trigger.changed` body as a trigger payload: the full payload, the
 * payload under `trigger`, or a bare trigger under `trigger`. Null otherwise.
 */
export function triggerChangedPayload(raw: unknown): {
  trigger: Trigger
  payload: TriggerOnePayload | null
} | null {
  const body = obj(raw)
  if (!body) return null
  const direct = body.kind === 'trigger' ? normalizeTriggerPayload(body) : null
  if (direct?.kind === 'trigger') return { trigger: direct.trigger, payload: direct }
  const inner = obj(body.trigger)
  if (!inner) return null
  if (inner.kind === 'trigger') {
    const nested = normalizeTriggerPayload(inner)
    return nested?.kind === 'trigger' ? { trigger: nested.trigger, payload: nested } : null
  }
  const trigger = normalizeTrigger(inner)
  return trigger ? { trigger, payload: null } : null
}

export interface TriggerMounterDeps {
  /** Fetch a trigger artifact body from its (authenticated) URL. */
  fetchPayload: (url: string) => Promise<unknown>
  /**
   * The gateway call ↻ and the settle refresh re-run a card's read through
   * (`trading.trigger.get` / `list` are agent-callable, so the web passes it
   * too). Default: none, no ↻ — unless `actions` is given, whose call then serves.
   */
  call?: TriggerCall
  /**
   * The trigger controls. An object, or a getter read at render and click
   * time (the desktop's chat becomes the desk without remounting). Absent → no
   * `.trigger-actions` at all.
   */
  actions?: TriggerActions | (() => TriggerActions | null | undefined) | null
  /** Copy the trigger id. Default: Clipboard API, then execCommand. */
  copyText?: (value: string) => void | Promise<void>
  /** Clock. Default: Date.now. */
  now?: () => number
  /** The diagnostics ring. Default: no-op. */
  diag?: (event: string, detail: Record<string, unknown>) => void
}

/**
 * Create the trigger card mounter bound to the transcript's fetch surface.
 *
 * `mountTrigger(root)` is idempotent: it only picks up placeholders it has not
 * claimed yet. It owns one clock — every second while a mounted card says
 * "checked N s ago" in seconds or a proposal expires within the hour, less
 * often otherwise — that re-derives the live lines and the "as of" stamps,
 * plus the short copy / confirm resets; `destroyAll` (alias `dispose`) clears
 * them all. One ResizeObserver keeps `data-trigger-layout` current.
 */
export function createTriggerMounter(deps: TriggerMounterDeps) {
  const diag = deps.diag ?? ((): void => {})
  const now = deps.now ?? ((): number => Date.now())
  const copyText = deps.copyText ?? copyLpText
  const getActions = (): TriggerActions | null => {
    const value = typeof deps.actions === 'function' ? deps.actions() : deps.actions
    return value && typeof value.call === 'function' ? value : null
  }
  const readCall = (): TriggerCall | null => deps.call ?? getActions()?.call ?? null
  const claimed = new Set<HTMLElement>()
  const rendered = new Set<HTMLElement>()
  const payloads = new Map<HTMLElement, TriggerPayload>()
  const refreshing = new Set<HTMLElement>()
  const wired = new WeakSet<HTMLElement>()
  /** Controls in flight, by trigger id; they outlive a re-render. */
  const busy = new Map<string, TriggerAction>()
  /** The last failed control's message, by trigger id, until the next click. */
  const errors = new Map<string, string>()
  /** Orders a "Fire now" placed, by order id → trigger id (refresh on settle). */
  const orders = new Map<string, string>()
  /**
   * Hosts whose card may not show the trigger as it is now: the artifact
   * snapshot while its live read is in flight (`checking`), or after that
   * read failed (`failed`). Their controls stay off (`data-trigger-stale`).
   */
  const stale = new Map<HTMLElement, TriggerStale>()
  /**
   * The newest live state of every trigger this mounter has seen (reads,
   * control answers, `trading.trigger.changed`), by id. A transcript
   * re-render re-mounts a card from its artifact file — the snapshot taken
   * when the trigger was created — so a re-mounted card draws this instead.
   * It holds no DOM and outlives `destroyAll`.
   */
  const latestByTriggerId = new Map<string, TriggerOnePayload>()
  const timers = new Set<ReturnType<typeof setTimeout>>()
  let clock: ReturnType<typeof setTimeout> | null = null
  const cards = new Map<HTMLElement, HTMLElement>()
  const dirty = new Set<HTMLElement>()
  let pending = false
  let cancelFrame: (() => void) | null = null
  const observer =
    typeof ResizeObserver === 'function'
      ? new ResizeObserver((entries) => {
          entries.forEach((entry) => dirty.add(entry.target as HTMLElement))
          scheduleLayout()
        })
      : null

  function scheduleLayout(): void {
    if (pending) return
    pending = true
    const run = (): void => {
      pending = false
      cancelFrame = null
      const batch = [...dirty]
      dirty.clear()
      batch.forEach((card) => {
        if (card.isConnected) layoutTriggerCard(card)
      })
    }
    if (typeof requestAnimationFrame === 'function') {
      const id = requestAnimationFrame(run)
      if (pending) cancelFrame = () => cancelAnimationFrame(id)
    } else {
      const id = setTimeout(run, 16)
      if (pending) cancelFrame = () => clearTimeout(id)
    }
  }

  function watch(host: HTMLElement): void {
    const card = host.querySelector<HTMLElement>('.trigger-card')
    if (!card) return
    const previous = cards.get(host)
    if (previous && previous !== card) observer?.unobserve(previous)
    cards.set(host, card)
    layoutTriggerCard(card)
    observer?.observe(card)
  }

  function unwatch(host: HTMLElement): void {
    const card = cards.get(host)
    if (!card) return
    observer?.unobserve(card)
    dirty.delete(card)
    cards.delete(host)
  }

  const ctx: TriggerRenderContext = {
    now,
    copyText,
    setTimer(fn, ms) {
      const id = setTimeout(() => {
        timers.delete(id)
        fn()
      }, ms)
      timers.add(id)
    },
    get canRefresh() {
      return readCall() !== null
    },
    get canWrite() {
      return getActions() !== null
    },
  }

  function triggersOf(payload: TriggerPayload): Trigger[] {
    return payload.kind === 'trigger' ? [payload.trigger] : payload.triggers
  }

  /* ── the live-state cache ── */

  function stampOf(trigger: Trigger): number {
    const at = Date.parse(trigger.updatedAt)
    return Number.isNaN(at) ? -Infinity : at
  }

  /** A trigger payload for a trigger that arrived without its envelope. */
  function envelopeFor(trigger: Trigger, fetchedAt: string): TriggerOnePayload {
    return {
      version: 1,
      fetchedAt,
      warnings: [],
      request: { kind: 'get', params: { triggerId: trigger.id } },
      kind: 'trigger',
      trigger,
      fire: null,
    }
  }

  /** Keep `fresh` unless the cache already holds a strictly newer state. */
  function rememberTrigger(fresh: TriggerOnePayload): void {
    const known = latestByTriggerId.get(fresh.trigger.id)
    if (known && stampOf(known.trigger) > stampOf(fresh.trigger)) return
    latestByTriggerId.set(fresh.trigger.id, { ...fresh, fire: null })
  }

  /** Feed a live payload into the cache. */
  function remember(payload: TriggerPayload): void {
    if (payload.kind === 'trigger') {
      rememberTrigger(payload)
      return
    }
    const at = payload.fetchedAt || new Date(now()).toISOString()
    payload.triggers.forEach((tr) => rememberTrigger(envelopeFor(tr, at)))
  }

  /**
   * A snapshot with every trigger the cache knows at least as fresh swapped
   * in. `live` is true when nothing of the snapshot's own state is left (a
   * trigger card with a cache hit, a list whose every row hit).
   */
  function withCache(snapshot: TriggerPayload): { payload: TriggerPayload; live: boolean } {
    const hit = (tr: Trigger): TriggerOnePayload | null => {
      const known = latestByTriggerId.get(tr.id)
      return known && stampOf(known.trigger) >= stampOf(tr) ? known : null
    }
    if (snapshot.kind === 'trigger') {
      const known = hit(snapshot.trigger)
      if (!known) return { payload: snapshot, live: false }
      return {
        payload: { ...known, request: snapshot.request ?? known.request, fire: null },
        live: true,
      }
    }
    let live = snapshot.triggers.length > 0
    const triggers = snapshot.triggers.map((tr) => {
      const known = hit(tr)
      if (!known) live = false
      return known ? known.trigger : tr
    })
    return { payload: { ...snapshot, triggers }, live }
  }

  /* ── the clock ── */

  function stopClock(): void {
    if (clock !== null) clearTimeout(clock)
    clock = null
  }

  function paintTimes(host: HTMLElement, payload: TriggerPayload, at: number): void {
    if (payload.kind === 'trigger') {
      const node = host.querySelector<HTMLElement>('.trigger-card__now')
      const next = nowText(payload.trigger, at)
      if (node && node.textContent !== next) node.textContent = next
    }
    host
      .querySelectorAll<HTMLElement>('[data-trigger-fetched-at], [data-trigger-at]')
      .forEach((node) => {
        const stamp = node.dataset.triggerFetchedAt ?? node.dataset.triggerAt ?? ''
        const next = relativeTime(stamp, at)
        if (next && node.textContent !== next) node.textContent = next
      })
  }

  function nextDelay(at: number): number {
    let delay = DCA_MINUTE_MS
    rendered.forEach((host) => {
      const payload = payloads.get(host)
      // A list row's line does not tick; only a trigger card's live line does.
      if (payload?.kind !== 'trigger') return
      delay = Math.min(delay, triggerTickDelay(payload.trigger, at))
    })
    return delay
  }

  function scheduleClock(): void {
    stopClock()
    if (rendered.size === 0) return
    clock = setTimeout(tick, nextDelay(now()))
  }

  function tick(): void {
    clock = null
    pruneDetached()
    if (rendered.size === 0) return
    const at = now()
    rendered.forEach((host) => {
      const payload = payloads.get(host)
      if (payload) paintTimes(host, payload, at)
    })
    scheduleClock()
  }

  /* ── controls ── */

  function paintControls(host: HTMLElement): void {
    const card = host.querySelector<HTMLElement>('.trigger-card')
    const staleState = stale.get(host)
    let anyBusy = false
    host.querySelectorAll<HTMLElement>('.trigger-actions').forEach((row) => {
      const id = row.dataset.triggerId ?? ''
      const action = busy.get(id)
      if (action) {
        anyBusy = true
        row.dataset.triggerBusy = action
        row.setAttribute('aria-busy', 'true')
      } else {
        delete row.dataset.triggerBusy
        row.removeAttribute('aria-busy')
      }
      row.querySelectorAll<HTMLButtonElement>('button[data-trigger-op]').forEach((b) => {
        b.disabled = Boolean(action) || staleState !== undefined
        if (action && b.dataset.triggerOp === action) b.dataset.triggerPending = 'true'
        else delete b.dataset.triggerPending
      })
      const line = row.querySelector<HTMLElement>('.trigger-actions__error')
      if (line) {
        const message = action ? '' : (errors.get(id) ?? '')
        line.textContent = message
        line.hidden = message === ''
      }
    })
    if (card) {
      if (anyBusy && card.dataset.triggerKind === 'trigger') card.dataset.triggerBusy = 'true'
      else delete card.dataset.triggerBusy
      paintStale(card, staleState)
    }
  }

  /** `data-trigger-stale` on the card, and the "may be stale · ↻" hint once the live read failed. */
  function paintStale(card: HTMLElement, state: TriggerStale | undefined): void {
    if (state) card.dataset.triggerStale = state
    else delete card.dataset.triggerStale
    const hint = card.querySelector<HTMLElement>(':scope > .trigger-card__stale')
    if (state !== 'failed') {
      hint?.remove()
      return
    }
    if (hint) return
    const foot = card.querySelector<HTMLElement>(':scope > .trigger-card__foot')
    card.insertBefore(staleHint(), foot)
  }

  function paintAll(): void {
    rendered.forEach(paintControls)
  }

  /** Render (or re-render) a payload into its host and keep it fitted. */
  function show(host: HTMLElement, payload: TriggerPayload): void {
    renderTrigger(host, payload, ctx)
    payloads.set(host, payload)
    paintControls(host)
    watch(host)
  }

  /**
   * Put a fresh trigger on every card that draws it: a trigger card of the
   * same id swaps to `fresh` (keeping its own ↻ request when `fresh` has
   * none), a list card replaces that row.
   */
  function applyTrigger(trigger: Trigger, fresh: TriggerOnePayload | null): void {
    const stamp = new Date(now()).toISOString()
    remember(fresh ?? envelopeFor(trigger, stamp))
    rendered.forEach((host) => {
      const shown = payloads.get(host)
      if (!shown || !host.isConnected) return
      if (shown.kind === 'trigger') {
        if (shown.trigger.id !== trigger.id) return
        // A live state: whatever snapshot the card held is gone.
        stale.delete(host)
        const next: TriggerOnePayload = fresh
          ? {
              ...fresh,
              request: fresh.request ?? shown.request,
              fetchedAt: fresh.fetchedAt || stamp,
            }
          : { ...shown, trigger, fire: null, fetchedAt: stamp }
        show(host, next)
      } else if (shown.triggers.some((tr) => tr.id === trigger.id)) {
        show(host, {
          ...shown,
          triggers: shown.triggers.map((tr) => (tr.id === trigger.id ? trigger : tr)),
        })
      }
    })
    scheduleClock()
  }

  /** First click on Stop / Fire now: say "click again" for a few seconds. */
  function armConfirm(button: HTMLButtonElement): void {
    const label = button.textContent ?? ''
    const title = button.title
    button.dataset.triggerConfirm = 'true'
    button.textContent = t('chat.triggerConfirm', { label })
    button.title =
      button.dataset.triggerOp === 'fire'
        ? t('chat.triggerFireConfirmTitle')
        : t('chat.triggerStopConfirmTitle')
    ctx.setTimer(() => {
      if (!button.isConnected || button.dataset.triggerConfirm !== 'true') return
      delete button.dataset.triggerConfirm
      button.textContent = label
      button.title = title
    }, TRIGGER_CONFIRM_MS)
  }

  async function act(button: HTMLButtonElement): Promise<void> {
    const actions = getActions()
    const id = button.dataset.triggerId ?? ''
    const action = button.dataset.triggerOp as TriggerAction
    if (!actions || !id || !ACTIONS.has(action) || busy.has(id)) return
    if (CONFIRMED.has(action) && button.dataset.triggerConfirm !== 'true') {
      armConfirm(button)
      return
    }
    busy.set(id, action)
    errors.delete(id)
    paintAll()
    diag('trigger.action.start', { action, triggerId: id })
    try {
      const raw = await actions.call(`trading.trigger.${action}`, { triggerId: id })
      const next = normalizeTriggerPayload(raw)
      if (!next || next.kind !== 'trigger') throw new Error(t('chat.triggerUnreadable'))
      busy.delete(id)
      applyTrigger(next.trigger, next)
      paintAll()
      const orderId = action === 'fire' ? next.fire?.orderId : null
      if (orderId) {
        orders.set(orderId, id)
        actions.onOrder?.(orderId)
      }
      diag('trigger.action.done', { action, triggerId: id, status: next.trigger.status })
    } catch (error) {
      busy.delete(id)
      errors.set(id, triggerErrorText(error))
      paintAll()
      diag('trigger.action.error', { action, error: String(error) })
      // "cannot approve …: it is done": the card is out of date. Re-read
      // every card that draws this trigger, controls off until it lands.
      const { code } = rpcError(error)
      if (code === 'trading.trigger.bad_state' || code === 'trading.trigger.not_found') {
        rendered.forEach((host) => {
          const payload = payloads.get(host)
          if (!payload || !host.isConnected) return
          if (!triggersOf(payload).some((tr) => tr.id === id)) return
          if (!readCall() || !requestFor(payload)) return
          stale.set(host, 'checking')
          paintControls(host)
          void refreshHost(host)
        })
      }
    }
  }

  /* ── ↻ refresh ── */

  function flashFootError(host: HTMLElement, message: string): void {
    const foot = host.querySelector<HTMLElement>('.trigger-card__foot')
    if (!foot) return
    let line = foot.querySelector<HTMLElement>('.trigger-card__refresh-error')
    if (!line) {
      line = el('span', 'trigger-card__refresh-error')
      line.setAttribute('role', 'alert')
      foot.append(line)
    }
    line.textContent = message
    const shown = line
    ctx.setTimer(() => shown.remove(), TRIGGER_ERROR_MS)
  }

  /** The read that re-draws `payload`: its echoed request, else `get` by id. */
  function requestFor(payload: TriggerPayload): TriggerRequest | null {
    if (payload.request) return payload.request
    if (payload.kind === 'trigger') {
      return { kind: 'get', params: { triggerId: payload.trigger.id } }
    }
    return null
  }

  /**
   * Re-run a card's read and redraw it. `quiet` (the read a mount fires on
   * its own) neither dims the card nor flashes a failure under the footer: a
   * stale card says so with its hint instead. `call` overrides the read call.
   */
  async function refreshHost(
    host: HTMLElement,
    opts: { quiet?: boolean; call?: TriggerCall | null } = {},
  ): Promise<void> {
    const current = payloads.get(host)
    const call = opts.call ?? readCall()
    const request = current ? requestFor(current) : null
    if (!current || !request || !call || refreshing.has(host)) return
    refreshing.add(host)
    const card = host.querySelector<HTMLElement>('.trigger-card')
    const buttons = [...host.querySelectorAll<HTMLButtonElement>('[data-trigger-foot="refresh"]')]
    if (!opts.quiet) {
      card?.setAttribute('data-trigger-refreshing', 'true')
      card?.setAttribute('aria-busy', 'true')
      buttons.forEach((b) => (b.disabled = true))
    }
    diag('trigger.refresh.start', { kind: request.kind, quiet: Boolean(opts.quiet) })
    try {
      const raw = await call(triggerReadMethod(request), request.params)
      const next = normalizeTriggerPayload(raw)
      if (!next) throw new Error(t('chat.triggerUnreadable'))
      if (!next.request) next.request = current.request
      if (!next.fetchedAt) next.fetchedAt = new Date(now()).toISOString()
      remember(next)
      if (!host.isConnected || !rendered.has(host)) return
      stale.delete(host)
      show(host, next)
      scheduleClock()
      diag('trigger.refresh.done', { kind: next.kind })
    } catch (error) {
      if (!opts.quiet) {
        card?.removeAttribute('data-trigger-refreshing')
        card?.removeAttribute('aria-busy')
        buttons.forEach((b) => (b.disabled = false))
        flashFootError(host, t('chat.triggerRefreshFailed', { message: rpcError(error).message }))
      }
      if (stale.has(host) && rendered.has(host)) {
        stale.set(host, 'failed')
        paintControls(host)
      }
      diag('trigger.refresh.error', { error: String(error) })
    } finally {
      refreshing.delete(host)
    }
  }

  /** One click handler per host: the controls, ↻. (Copy wires itself.) */
  function wire(host: HTMLElement): void {
    if (wired.has(host)) return
    wired.add(host)
    host.addEventListener('click', (event) => {
      const target = (event.target as Element | null)?.closest<HTMLElement>(
        '[data-trigger-op], [data-trigger-foot]',
      )
      if (!target || !host.contains(target) || (target as HTMLButtonElement).disabled) return
      if (target.dataset.triggerFoot === 'refresh') {
        void refreshHost(host)
        return
      }
      if (target.dataset.triggerOp) void act(target as HTMLButtonElement)
    })
  }

  /* ── events ── */

  /**
   * `trading.order.finished`: re-read every card that shows that order (a
   * parked sell settling, a "Fire now" landing). `triggerId` is the order's
   * own `triggerId` when the event carries it.
   */
  function orderFinished(orderId: string, triggerId?: string | null): void {
    if (!orderId) return
    const placedBy = orders.get(orderId)
    orders.delete(orderId)
    rendered.forEach((host) => {
      const payload = payloads.get(host)
      if (!payload || !host.isConnected) return
      const hit = triggersOf(payload).some(
        (tr) =>
          tr.id === placedBy ||
          (Boolean(triggerId) && tr.id === triggerId) ||
          tr.result?.orderId === orderId ||
          tr.fires.some((f) => f.orderId === orderId),
      )
      if (hit) void refreshHost(host)
    })
  }

  /** `trading.trigger.changed`: swap the trigger into every card that draws it. */
  function triggerChanged(raw: unknown): void {
    const changed = triggerChangedPayload(raw)
    if (!changed) return
    diag('trigger.changed', { triggerId: changed.trigger.id, status: changed.trigger.status })
    applyTrigger(changed.trigger, changed.payload)
  }

  /* ── mount ── */

  function pruneDetached(): void {
    for (const host of [...claimed]) {
      if (!host.isConnected) {
        claimed.delete(host)
        rendered.delete(host)
        payloads.delete(host)
        stale.delete(host)
        unwatch(host)
      }
    }
  }

  function setStatus(host: HTMLElement, message: string): void {
    const status = host.querySelector<HTMLElement>('.msg-artifact-trigger__status')
    if (!status) return
    status.textContent = message
    status.hidden = message === ''
  }

  async function mountOne(host: HTMLElement): Promise<void> {
    const url = host.dataset.triggerSrc || ''
    if (!url) {
      setStatus(host, t('chat.triggerUnavailable'))
      return
    }
    try {
      diag('trigger.mount.start', { url })
      const raw = await deps.fetchPayload(url)
      const payload = normalizeTriggerPayload(raw)
      if (!payload) {
        setStatus(host, t('chat.triggerUnreadable'))
        diag('trigger.mount.empty', { url })
        return
      }
      if (!host.isConnected || !claimed.has(host)) {
        claimed.delete(host)
        return
      }
      // The artifact is a snapshot from when it was published: draw the
      // newest state this mounter knows instead, and re-read the live one.
      // Until that read lands, a card still holding snapshot state keeps its
      // controls off — an "Approve & arm" on a done trigger must not be
      // clickable.
      const { payload: shown, live } = withCache(payload)
      const call = deps.call ?? null
      const reread = call !== null && requestFor(shown) !== null
      if (reread && !live) stale.set(host, 'checking')
      wire(host)
      rendered.add(host)
      show(host, shown)
      setStatus(host, '')
      scheduleClock()
      diag('trigger.mount.done', { url, kind: payload.kind, cached: live })
      if (reread) void refreshHost(host, { quiet: true, call })
    } catch (error) {
      setStatus(host, t('chat.triggerFailed'))
      diag('trigger.mount.error', { url, error: String(error) })
    }
  }

  /** Mount every not-yet-mounted trigger placeholder inside `root`. */
  function mountTrigger(root: HTMLElement | null | undefined): void {
    if (!root) return
    pruneDetached()
    root.querySelectorAll<HTMLElement>('[data-trigger-src]').forEach((host) => {
      if (claimed.has(host)) return
      claimed.add(host)
      void mountOne(host)
    })
  }

  /** Forget every host and clear every timer (route unmount). */
  function destroyAll(): void {
    claimed.clear()
    rendered.clear()
    payloads.clear()
    refreshing.clear()
    stale.clear()
    busy.clear()
    errors.clear()
    orders.clear()
    stopClock()
    observer?.disconnect()
    cards.clear()
    dirty.clear()
    cancelFrame?.()
    cancelFrame = null
    pending = false
    timers.forEach((id) => clearTimeout(id))
    timers.clear()
  }

  return {
    mountTrigger,
    destroyAll,
    dispose: destroyAll,
    pruneDetached,
    orderFinished,
    triggerChanged,
    refresh: refreshHost,
  }
}

export type TriggerMounter = ReturnType<typeof createTriggerMounter>
