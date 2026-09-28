import type { MessageKey } from '~/i18n'
import { sameAddress } from '../logic'
import type { Mandate, Wallet } from '../types'
import type { MissionPreset } from './presets'

/**
 * DCA mandates on the desk (docs/dca.md): the engine owns the schedule, the
 * cap and the stop rules, so nothing here counts money. These helpers only
 * turn the engine's figures into words and the contract form into RPC params.
 */

const BASE = 8453
const ROBINHOOD = 4663

/** The catalogue entries that create a mandate instead of a cron job. */
const MANDATE_PRESET_IDS: ReadonlySet<string> = new Set(['dca', 'dca-capped'])

export function isMandatePreset(preset: MissionPreset | null | undefined): boolean {
  return Boolean(preset && MANDATE_PRESET_IDS.has(preset.id))
}

/** The cadence chips. The desk offers hourly and up; the CLI goes down to a minute. */
export const DCA_EVERY: readonly { seconds: number; key: string }[] = [
  { seconds: 3_600, key: '1h' },
  { seconds: 21_600, key: '6h' },
  { seconds: 43_200, key: '12h' },
  { seconds: 86_400, key: '1d' },
  { seconds: 604_800, key: '1w' },
]

export const DCA_MIN_EVERY_S = 3_600

const UNIT_S: Record<string, number> = { s: 1, m: 60, h: 3_600, d: 86_400, w: 604_800 }

/** `30m`, `2h`, `1d`, `1w` or a plain number of seconds, as the CLI reads `--every`. */
export function parseEvery(text: string): number | null {
  const m = /^\s*(\d+(?:\.\d+)?)\s*([smhdw]?)\s*$/i.exec(text)
  if (!m) return null
  const n = Number(m[1])
  const unit = UNIT_S[(m[2] || 's').toLowerCase()] ?? 1
  const seconds = Math.round(n * unit)
  return Number.isFinite(seconds) && seconds > 0 ? seconds : null
}

/** The shortest `--every` word for a cadence: 86400 → "1d", 5400 → "90m". */
export function everyWord(seconds: number): string {
  for (const [unit, size] of [
    ['w', 604_800],
    ['d', 86_400],
    ['h', 3_600],
    ['m', 60],
  ] as const) {
    if (seconds >= size && seconds % size === 0) return `${seconds / size}${unit}`
  }
  return String(seconds)
}

/** "day", "week", "hour", "6 h", "30 min": what follows the slash in "$10 / day". */
export function everyShort(seconds: number): string {
  if (seconds === 86_400) return 'day'
  if (seconds === 604_800) return 'week'
  if (seconds === 3_600) return 'hour'
  if (seconds % 604_800 === 0) return `${seconds / 604_800} w`
  if (seconds % 86_400 === 0) return `${seconds / 86_400} d`
  if (seconds % 3_600 === 0) return `${seconds / 3_600} h`
  if (seconds % 60 === 0) return `${seconds / 60} min`
  return `${seconds} s`
}

/** "every day", "every 6 hours", "every 30 minutes". */
export function everyPhrase(seconds: number): string {
  if (seconds === 86_400) return 'every day'
  if (seconds === 604_800) return 'every week'
  if (seconds === 3_600) return 'every hour'
  const plural = (n: number, unit: string) => `every ${n} ${unit}${n === 1 ? '' : 's'}`
  if (seconds % 604_800 === 0) return plural(seconds / 604_800, 'week')
  if (seconds % 86_400 === 0) return plural(seconds / 86_400, 'day')
  if (seconds % 3_600 === 0) return plural(seconds / 3_600, 'hour')
  if (seconds % 60 === 0) return plural(seconds / 60, 'minute')
  return plural(seconds, 'second')
}

const usdWhole = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  maximumFractionDigits: 0,
})
const usdCents = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})

/** "$120" for whole dollars, "$120.40" otherwise, "—" when unknown. */
export function usdShort(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return Math.abs(value - Math.round(value)) < 0.005
    ? usdWhole.format(Math.round(value))
    : usdCents.format(value)
}

/** The mini progress: "$120 / $300". */
export function mandateProgressText(m: Mandate): string {
  return `${usdShort(m.budget.spentUsd)} / ${usdShort(m.budget.capUsd)}`
}

/** spent / cap as a 0–1 fraction for a bar, clamped. */
export function mandateProgress(m: Mandate): number {
  const p = Number.isFinite(m.budget.progress)
    ? m.budget.progress
    : m.budget.capUsd > 0
      ? m.budget.spentUsd / m.budget.capUsd
      : 0
  return Math.min(1, Math.max(0, p))
}

/** "3 h 12 m", "12 m", "2 d 4 h", "under a minute". */
export function countdown(ms: number): string {
  if (ms < 60_000) return 'under a minute'
  const minutes = Math.floor(ms / 60_000)
  if (minutes < 60) return `${minutes} m`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return minutes % 60 ? `${hours} h ${minutes % 60} m` : `${hours} h`
  const days = Math.floor(hours / 24)
  return hours % 24 ? `${days} d ${hours % 24} h` : `${days} d`
}

export interface MandateState {
  /** The state word, from the catalogue. */
  key: MessageKey
  /** Active only: "3 h 12 m" to the next buy, or null when it is due now or unknown. */
  next: string | null
  /** Active only: the next buy's moment has passed; the engine fires it on its next tick. */
  due: boolean
}

const STATE_KEYS: Record<Mandate['status'], MessageKey> = {
  awaiting_approval: 'trading.dca.state.awaiting',
  active: 'trading.dca.state.active',
  paused: 'trading.dca.state.paused',
  completed: 'trading.dca.state.done',
  stopped: 'trading.dca.state.stopped',
  rejected: 'trading.dca.state.rejected',
  expired: 'trading.dca.state.expired',
}

/** What the mandate is doing, as the Missions rows say it. */
export function mandateState(m: Mandate, now: number): MandateState {
  const key = STATE_KEYS[m.status] ?? 'trading.dca.state.done'
  if (m.status !== 'active') return { key, next: null, due: false }
  const at = m.schedule.nextRunAt ? Date.parse(m.schedule.nextRunAt) : Number.NaN
  if (!Number.isFinite(at)) return { key, next: null, due: false }
  if (at <= now) return { key, next: null, due: true }
  return { key, next: countdown(at - now), due: false }
}

/** How long a finished mandate stays in the Missions list, so its "Done" is seen. */
export const DONE_VISIBLE_MS = 24 * 3_600_000

/**
 * The mandates a desk lists beside its cron missions: the ones filed to this
 * session, plus the unfiled ones (created from the CLI, which belong to the
 * operator and so to every desk). Live ones always; a completed one for a day,
 * so its "Done" is read where it ran.
 */
export function deskMandates(all: readonly Mandate[], sessionKey: string, now: number): Mandate[] {
  return all.filter((m) => {
    if (m.sessionKey && m.sessionKey !== sessionKey) return false
    if (m.status === 'awaiting_approval' || m.status === 'active' || m.status === 'paused')
      return true
    if (m.status !== 'completed') return false
    const at = Date.parse(m.updatedAt)
    return Number.isFinite(at) && now - at < DONE_VISIBLE_MS
  })
}

/* ── The DCA contract form ───────────────────────────────────────────────── */

export interface DcaForm {
  token: string
  /** What is spent; empty = the chain's USDC (required on Robinhood Chain). */
  quote: string
  usd: string
  everySeconds: number
  /** The custom cadence as typed ("30m", "2d"); null while a chip is chosen. */
  everyCustom: string | null
  cap: string
  runs: string
  maxPrice: string
  wallet: string
  chainId: number
  name: string
  startNow: boolean
}

export type DcaError =
  'token' | 'quote' | 'usd' | 'every' | 'cap' | 'capBelow' | 'runs' | 'maxPrice'

function positive(text: string): number | null {
  const n = Number(text.replace(/,/g, '').trim())
  return text.trim() && Number.isFinite(n) && n > 0 ? n : null
}

/** The name a DCA gets from its token (and ceiling), so two DCAs are not both "DCA". */
export function dcaAutoName(form: Pick<DcaForm, 'token' | 'maxPrice'>): string {
  const token = form.token.trim()
  const symbol = /^0x[0-9a-fA-F]{40}$/.test(token)
    ? `${token.slice(0, 6)}…`
    : token.toUpperCase() || 'ETH'
  const max = positive(form.maxPrice)
  return max !== null ? `DCA ${symbol} under ${max}` : `DCA ${symbol}`
}

/** The form a preset opens: its knobs (token, USD per buy, ceiling), a day apart, $300 cap. */
export function dcaFormFromPreset(
  preset: MissionPreset | null,
  params: Readonly<Record<string, string>>,
  ctx: { primary: string | null },
): DcaForm {
  const token = (params.token ?? 'ETH').trim().toUpperCase() || 'ETH'
  const usd = positive(params.usd ?? '') !== null ? (params.usd ?? '').trim() : '10'
  const maxPrice = preset?.id === 'dca-capped' ? (params.maxPrice ?? '').trim() : ''
  const interval = preset?.interval
  const form: DcaForm = {
    token,
    quote: '',
    usd,
    everySeconds:
      interval?.kind === 'every' && interval.seconds >= DCA_MIN_EVERY_S ? interval.seconds : 86_400,
    everyCustom: null,
    cap: preset?.budgetTotalUsd || '300',
    runs: '',
    maxPrice,
    wallet: ctx.primary ?? '',
    chainId: BASE,
    name: '',
    startNow: true,
  }
  return { ...form, name: dcaAutoName(form) }
}

/** An edit starts from what the mandate is. */
export function dcaFormFromMandate(m: Mandate): DcaForm {
  const every = m.schedule.everySeconds
  return {
    token: m.token.symbol || m.token.address,
    quote: m.quote.symbol || m.quote.address,
    usd: String(m.budget.usdPerRun),
    everySeconds: every,
    everyCustom: DCA_EVERY.some((c) => c.seconds === every) ? null : everyWord(every),
    cap: String(m.budget.capUsd),
    runs: m.runs.max !== null ? String(m.runs.max) : '',
    maxPrice: m.guards.maxPriceUsd !== null ? String(m.guards.maxPriceUsd) : '',
    wallet: m.wallet.address,
    chainId: m.chain.id,
    name: m.name,
    startNow: m.schedule.startNow,
  }
}

/** The cadence the form stands for: the chip, or the custom text parsed. */
export function dcaEverySeconds(form: DcaForm): number | null {
  return form.everyCustom !== null ? parseEvery(form.everyCustom) : form.everySeconds
}

/** The cap the engine will hold: the typed one, else USD per buy × buys (its own default). */
export function dcaCapUsd(form: DcaForm): number | null {
  const cap = positive(form.cap)
  if (cap !== null) return cap
  const usd = positive(form.usd)
  const runs = positive(form.runs)
  return usd !== null && runs !== null ? usd * Math.floor(runs) : null
}

export function validateDca(form: DcaForm): { ok: boolean; error?: DcaError } {
  if (!form.token.trim()) return { ok: false, error: 'token' }
  if (form.chainId === ROBINHOOD && !form.quote.trim()) return { ok: false, error: 'quote' }
  const usd = positive(form.usd)
  if (usd === null) return { ok: false, error: 'usd' }
  const every = dcaEverySeconds(form)
  if (every === null || every < DCA_MIN_EVERY_S) return { ok: false, error: 'every' }
  if (form.runs.trim() && (positive(form.runs) === null || !Number.isInteger(Number(form.runs))))
    return { ok: false, error: 'runs' }
  if (form.cap.trim() && positive(form.cap) === null) return { ok: false, error: 'cap' }
  if (!form.cap.trim() && !form.runs.trim()) return { ok: false, error: 'cap' }
  const cap = positive(form.cap)
  if (cap !== null && cap < usd) return { ok: false, error: 'capBelow' }
  if (form.maxPrice.trim() && positive(form.maxPrice) === null)
    return { ok: false, error: 'maxPrice' }
  return { ok: true }
}

/** `trading.dca.create` params. The desk's connection is the operator's: it starts `active`. */
export function dcaCreateParams(
  form: DcaForm,
  ctx: { sessionKey: string; wallets: readonly Wallet[] },
): Record<string, unknown> {
  const usd = positive(form.usd)
  const cap = positive(form.cap)
  const runs = positive(form.runs)
  const max = positive(form.maxPrice)
  const wallet =
    ctx.wallets.find((w) => sameAddress(w.address, form.wallet))?.address ?? form.wallet
  const params: Record<string, unknown> = {
    chainId: form.chainId,
    token: form.token.trim(),
    usdPerRun: usd,
    everySeconds: dcaEverySeconds(form),
    name: form.name.trim() || dcaAutoName(form),
    startNow: form.startNow,
  }
  if (form.quote.trim()) params.quote = form.quote.trim()
  if (cap !== null) params.capUsd = cap
  if (runs !== null) params.runsMax = Math.floor(runs)
  if (max !== null) params.maxPriceUsd = max
  if (wallet) params.wallet = wallet
  if (ctx.sessionKey) params.sessionKey = ctx.sessionKey
  return params
}

/**
 * `trading.dca.update` params: only what changed, and only what the engine
 * lets an update change (usdPerRun, capUsd, runsMax, everySeconds,
 * maxPriceUsd, name). Clearing a limit is a 0: `runsMax: 0` drops the run
 * limit, `maxPriceUsd: 0` drops the price guard.
 */
export function dcaUpdatePatch(form: DcaForm, m: Mandate): Record<string, unknown> {
  const patch: Record<string, unknown> = {}
  const usd = positive(form.usd)
  if (usd !== null && usd !== m.budget.usdPerRun) patch.usdPerRun = usd
  const cap = positive(form.cap)
  if (cap !== null && cap !== m.budget.capUsd) patch.capUsd = cap
  const runs = form.runs.trim() ? Math.floor(Number(form.runs)) : null
  if (runs !== null && runs !== m.runs.max) patch.runsMax = runs
  else if (runs === null && m.runs.max !== null) patch.runsMax = 0
  const every = dcaEverySeconds(form)
  if (every !== null && every !== m.schedule.everySeconds) patch.everySeconds = every
  const max = positive(form.maxPrice)
  if (max !== null && max !== m.guards.maxPriceUsd) patch.maxPriceUsd = max
  else if (max === null && m.guards.maxPriceUsd !== null) patch.maxPriceUsd = 0
  const name = form.name.trim()
  if (name && name !== m.name) patch.name = name
  return patch
}
