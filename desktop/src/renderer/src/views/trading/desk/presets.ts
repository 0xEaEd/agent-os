import {
  ArrowDownRight,
  Bell,
  CalendarClock,
  Inbox,
  LineChart,
  PiggyBank,
  Scale,
  TrendingDown,
  TrendingUp,
  type LucideIcon,
} from 'lucide-react'
import type { MessageKey } from '~/i18n'
import type { Interval, MissionForm, StopRule } from './desk-logic'

/**
 * Ready-made missions: pick one, change two or three numbers, start. The
 * catalogue mirrors `views/jobs/blueprints.ts` — the same idea for the same
 * reason — but a mission spends money, so each preset also declares whether
 * it trades at all and what it cannot promise.
 *
 * Every preset is written against commands the wallet-trading skill actually
 * has (`quote`, `swap`, `tokens`, `portfolio`, `orders`, `limits`). Nothing
 * here asks the agent for a signal it has no way to read.
 */

const BASE = 8453

export type KnobKind = 'token' | 'ticker' | 'usd' | 'pct' | 'price' | 'minutes' | 'choice'

export interface Knob {
  key: string
  kind: KnobKind
  label: MessageKey
  /** The starting value, as typed into the field. */
  value: string
  /** `choice` only: the values offered, each with its own label. */
  options?: readonly { value: string; label: MessageKey }[]
}

export type PresetGroup = 'watch' | 'schedule' | 'react'

export interface MissionPreset {
  id: string
  group: PresetGroup
  name: MessageKey
  hint: MessageKey
  icon: LucideIcon
  /**
   * True when the mission only reads and reports. A watch-only mission is
   * the one safe way in: it proves the whole machine works before any of
   * the user's money is at stake.
   */
  readOnly: boolean
  knobs: readonly Knob[]
  /** The goal sentence, built from the knob values. */
  goal: (p: Readonly<Record<string, string>>) => string
  /** The name the mission gets, so two DCAs are not both called "DCA". */
  title: (p: Readonly<Record<string, string>>) => string
  interval: Interval
  chains?: number[]
  budgetTotalUsd?: string
  budgetPerOrderUsd?: string
  stop?: StopRule
  /** Said in the contract when the preset promises less than its name implies. */
  caveat?: MessageKey
}

const every = (seconds: number): Interval => ({ kind: 'every', seconds })

/** Knob values, plainly: an empty or junk number must not silently become "0". */
function num(p: Readonly<Record<string, string>>, key: string, fallback: string): string {
  const raw = (p[key] ?? '').replace(/,/g, '').trim()
  const n = Number(raw)
  return Number.isFinite(n) && n > 0 ? raw : fallback
}

function sym(p: Readonly<Record<string, string>>, key: string, fallback: string): string {
  const raw = (p[key] ?? '').trim().toUpperCase()
  return raw || fallback
}

export const MISSION_PRESETS: readonly MissionPreset[] = [
  /* ── Watch only: no swap, no spend ───────────────────────────────────── */
  {
    id: 'portfolio-report',
    group: 'watch',
    name: 'trading.preset.portfolioReport.name',
    hint: 'trading.preset.portfolioReport.hint',
    icon: LineChart,
    readOnly: true,
    knobs: [],
    title: () => 'Portfolio report',
    goal: () =>
      'Report the portfolio and nothing else. Run `agentos trade portfolio --json` and give: total value, ' +
      'unrealized and realized PnL, and the two holdings that moved the most since the last run. ' +
      'Do not quote and do not swap. If a holding has no cost basis, say so rather than inventing one.',
    interval: every(86_400),
  },
  {
    id: 'price-alert',
    group: 'watch',
    name: 'trading.preset.priceAlert.name',
    hint: 'trading.preset.priceAlert.hint',
    icon: Bell,
    readOnly: true,
    knobs: [
      { key: 'token', kind: 'token', label: 'trading.knob.token', value: 'ETH' },
      {
        key: 'direction',
        kind: 'choice',
        label: 'trading.knob.direction',
        value: 'below',
        options: [
          { value: 'below', label: 'trading.knob.direction.below' },
          { value: 'above', label: 'trading.knob.direction.above' },
        ],
      },
      { key: 'price', kind: 'price', label: 'trading.knob.price', value: '2300' },
    ],
    title: (p) => `${sym(p, 'token', 'ETH')} alert`,
    goal: (p) =>
      `Watch the price of ${sym(p, 'token', 'ETH')}. Read it with \`agentos trade tokens\` (or a quote) and ` +
      `compare it with ${num(p, 'price', '2300')} USD. If it is ${p.direction === 'above' ? 'at or above' : 'at or below'} ` +
      `that level, say so in one line with the price you saw; otherwise reply "no alert" in one line. ` +
      'Never swap: this mission only watches. Alert once per crossing: after you have alerted, reply `no alert` ' +
      `until the price has moved back at least 1% to the other side of ${num(p, 'price', '2300')}. Say the price every run.`,
    interval: every(900),
  },
  {
    id: 'drawdown-alert',
    group: 'watch',
    name: 'trading.preset.drawdownAlert.name',
    hint: 'trading.preset.drawdownAlert.hint',
    icon: ArrowDownRight,
    readOnly: true,
    knobs: [
      { key: 'token', kind: 'token', label: 'trading.knob.token', value: 'ETH' },
      { key: 'dropPct', kind: 'pct', label: 'trading.knob.dropPct', value: '10' },
    ],
    title: (p) => `${sym(p, 'token', 'ETH')} drawdown`,
    goal: (p) =>
      `Watch ${sym(p, 'token', 'ETH')} for a fall. Read the holding with \`agentos trade portfolio --json\` and ` +
      `report when its unrealized return is at or below -${num(p, 'dropPct', '10')}%, naming the percentage and ` +
      'the average cost you compared against. Otherwise reply "no alert" in one line. Never swap: telling the ' +
      'user is the whole job.',
    interval: every(300),
    caveat: 'trading.preset.drawdownAlert.caveat',
  },
  {
    id: 'approval-watch',
    group: 'watch',
    name: 'trading.preset.approvalWatch.name',
    hint: 'trading.preset.approvalWatch.hint',
    icon: Inbox,
    readOnly: true,
    knobs: [{ key: 'minutes', kind: 'minutes', label: 'trading.knob.minutes', value: '30' }],
    title: () => 'Approval watch',
    goal: (p) =>
      'Check for orders still waiting on the user. Run `agentos trade orders --status awaiting_approval --json`; ' +
      `if any has been waiting longer than ${num(p, 'minutes', '30')} minutes, list it with its id, what it buys and ` +
      'when it expires. Otherwise reply "nothing waiting". Never approve, reject or swap anything yourself.',
    interval: every(900),
  },

  /* ── On a schedule: buys money can be put behind ─────────────────────── */
  {
    id: 'dca',
    group: 'schedule',
    name: 'trading.preset.dca.name',
    hint: 'trading.preset.dca.hint',
    icon: CalendarClock,
    readOnly: false,
    knobs: [
      { key: 'token', kind: 'token', label: 'trading.knob.token', value: 'ETH' },
      { key: 'usd', kind: 'usd', label: 'trading.knob.usdPerRun', value: '10' },
    ],
    title: (p) => `DCA ${sym(p, 'token', 'ETH')}`,
    goal: (p) =>
      `Buy ${num(p, 'usd', '10')} USD of ${sym(p, 'token', 'ETH')} with USDC on Base this run, once. ` +
      'Quote first, then swap, and report the order id.',
    interval: every(86_400),
    budgetTotalUsd: '300',
    budgetPerOrderUsd: '10',
    stop: { kind: 'runs', runs: 30 },
  },
  {
    id: 'dca-capped',
    group: 'schedule',
    name: 'trading.preset.dcaCapped.name',
    hint: 'trading.preset.dcaCapped.hint',
    icon: PiggyBank,
    readOnly: false,
    knobs: [
      { key: 'token', kind: 'token', label: 'trading.knob.token', value: 'ETH' },
      { key: 'usd', kind: 'usd', label: 'trading.knob.usdPerRun', value: '10' },
      { key: 'maxPrice', kind: 'price', label: 'trading.knob.maxPrice', value: '3000' },
    ],
    title: (p) => `DCA ${sym(p, 'token', 'ETH')} under ${num(p, 'maxPrice', '3000')}`,
    goal: (p) =>
      `Buy ${num(p, 'usd', '10')} USD of ${sym(p, 'token', 'ETH')} with USDC on Base this run, but only while ` +
      `${sym(p, 'token', 'ETH')} is at or below ${num(p, 'maxPrice', '3000')} USD. Read the price first; if it is above ` +
      'that, skip this run and say the price you saw. Never buy twice in one run.',
    interval: every(86_400),
    budgetTotalUsd: '300',
    budgetPerOrderUsd: '10',
    stop: { kind: 'runs', runs: 30 },
  },

  /* ── Reacting to price, and keeping the shape of the book ────────────── */
  {
    id: 'dip',
    group: 'react',
    name: 'trading.preset.dip.name',
    hint: 'trading.preset.dip.hint',
    icon: TrendingDown,
    readOnly: false,
    knobs: [
      { key: 'token', kind: 'token', label: 'trading.knob.token', value: 'ETH' },
      { key: 'price', kind: 'price', label: 'trading.knob.triggerPrice', value: '2300' },
      { key: 'usd', kind: 'usd', label: 'trading.knob.usdPerRun', value: '25' },
    ],
    title: (p) => `Buy ${sym(p, 'token', 'ETH')} at ${num(p, 'price', '2300')}`,
    goal: (p) =>
      `If ${sym(p, 'token', 'ETH')} is at or below ${num(p, 'price', '2300')} USD, buy ${num(p, 'usd', '25')} USD of it ` +
      'with USDC on Base once, then reply `MISSION COMPLETE` on its own line. Otherwise do nothing and say the price ' +
      'you saw and the trigger.',
    interval: every(300),
    budgetTotalUsd: '100',
    budgetPerOrderUsd: '25',
    stop: { kind: 'goal' },
  },
  {
    id: 'take-profit',
    group: 'react',
    name: 'trading.preset.takeProfit.name',
    hint: 'trading.preset.takeProfit.hint',
    icon: TrendingUp,
    readOnly: false,
    knobs: [
      { key: 'token', kind: 'token', label: 'trading.knob.token', value: 'ETH' },
      { key: 'gainPct', kind: 'pct', label: 'trading.knob.gainPct', value: '25' },
      { key: 'sellPct', kind: 'pct', label: 'trading.knob.sellPct', value: '50' },
    ],
    title: (p) => `Take profit on ${sym(p, 'token', 'ETH')}`,
    goal: (p) =>
      `Read the ${sym(p, 'token', 'ETH')} holding with \`agentos trade portfolio --json\`. If its unrealized return is ` +
      `at or above +${num(p, 'gainPct', '25')}%, sell ${num(p, 'sellPct', '50')}% of the position into USDC on Base ` +
      'once (`--pct`), then reply `MISSION COMPLETE`. Otherwise do nothing and report the return you saw. ' +
      'Reference: the unrealized % comes from `unrealizedPct` of that holding in `agentos trade portfolio --json`; ' +
      'never sell a holding whose `priceUsd` is null.',
    interval: every(3600),
    budgetTotalUsd: '',
    budgetPerOrderUsd: '',
    stop: { kind: 'goal' },
  },
  {
    id: 'rebalance',
    group: 'react',
    name: 'trading.preset.rebalance.name',
    hint: 'trading.preset.rebalance.hint',
    icon: Scale,
    readOnly: false,
    knobs: [
      { key: 'token', kind: 'token', label: 'trading.knob.token', value: 'ETH' },
      { key: 'targetPct', kind: 'pct', label: 'trading.knob.targetPct', value: '70' },
      { key: 'driftPct', kind: 'pct', label: 'trading.knob.driftPct', value: '5' },
    ],
    title: (p) => `Rebalance ${sym(p, 'token', 'ETH')}/USDC`,
    goal: (p) => {
      const token = sym(p, 'token', 'ETH')
      const target = Number(num(p, 'targetPct', '70'))
      const drift = Number(num(p, 'driftPct', '5'))
      const low = String(Math.max(0, target - drift))
      const high = String(Math.min(100, target + drift))
      return (
        `Keep ${token} between ${low}% and ${high}% of (${token}+USDC) value on Base, ignoring every other token. ` +
        `Read the split with \`agentos trade portfolio --json\`. If ${token} is above the band, sell just enough ${token} ` +
        `to reach ${target}%; if below, buy just enough with USDC. One swap per run at most, and never spend the last ` +
        '0.002 ETH (gas). Inside the band do nothing and say the split.'
      )
    },
    interval: every(3600),
    budgetTotalUsd: '500',
    budgetPerOrderUsd: '100',
  },
]

export const PRESET_GROUPS: readonly PresetGroup[] = ['watch', 'schedule', 'react']

export function presetById(id: string): MissionPreset | null {
  return MISSION_PRESETS.find((p) => p.id === id) ?? null
}

/** The knob values a preset starts with. */
export function presetDefaults(preset: MissionPreset): Record<string, string> {
  return Object.fromEntries(preset.knobs.map((k) => [k.key, k.value]))
}

/**
 * A preset plus its knob values, as the contract form. Watch-only missions
 * carry no budget. Dry run stays off by default: the contract keeps the
 * manual toggle for a user who wants the first run held back.
 */
export function formFromPreset(
  preset: MissionPreset,
  params: Readonly<Record<string, string>>,
  ctx: { primary: string | null; chains?: number[] },
): MissionForm {
  const chains = preset.chains ?? (ctx.chains?.length ? ctx.chains : [BASE])
  return {
    kind: 'custom',
    name: preset.title(params),
    goal: preset.goal(params),
    wallets: ctx.primary ? [ctx.primary] : [],
    chains,
    budgetTotalUsd: preset.readOnly ? '' : (preset.budgetTotalUsd ?? ''),
    budgetPerOrderUsd: preset.readOnly ? '' : (preset.budgetPerOrderUsd ?? ''),
    stop: preset.stop ?? { kind: 'none' },
    interval: preset.interval,
    dryRun: false,
  }
}
