import type { TrayNextMandate, TraySummary } from '@shared/tray'
import { isAwaitingApproval } from '~/views/trading/logic'
import type { Mandate, Order } from '~/views/trading/types'

/**
 * The pure half of the menu bar summary (`use-tray-summary.ts` gathers the
 * inputs): what is waiting on the user and the next DCA buy, in the shape
 * main's `tray:summary` validates.
 */

export interface TraySources {
  /** Sessions with a turn streaming (`useLive`). */
  liveTurns: number
  /** The console's approval monitor: tool approvals pending. */
  toolApprovals: number
  /** The desk's orders awaiting approval, as `trading.orders.list` returned them. */
  orders: readonly Pick<Order, 'status'>[]
  /** The engine's own count of awaiting orders, when it sent one. */
  ordersPending: number
  /** Live DCA mandates (`trading.dca.list`). */
  mandates: readonly Mandate[]
}

/** "ETH → USDC": what a mandate spends, then what it buys; its name when unnamed tokens. */
export function mandateLabel(m: Pick<Mandate, 'name' | 'token' | 'quote'>): string {
  const spend = m.quote?.symbol?.trim()
  const buy = m.token?.symbol?.trim()
  if (spend && buy) return `${spend} → ${buy}`
  return m.name?.trim() || buy || 'DCA'
}

/**
 * The active mandate whose buy comes first. An overdue one is still the
 * soonest (main says "due now"); paused and awaiting ones never buy on
 * their own, so they have no next buy to show.
 */
export function nextMandate(mandates: readonly Mandate[]): TrayNextMandate | null {
  let best: { m: Mandate; at: number } | null = null
  for (const m of mandates) {
    if (m.status !== 'active' || !m.schedule?.nextRunAt) continue
    const at = Date.parse(m.schedule.nextRunAt)
    if (!Number.isFinite(at)) continue
    if (!best || at < best.at) best = { m, at }
  }
  return best ? { label: mandateLabel(best.m), at: new Date(best.at).toISOString() } : null
}

/**
 * Everything the menu says about the gateway's work. Desk approvals are the
 * orders awaiting a decision plus the mandates awaiting approval: both wait
 * on the user at the desk, which is where the approvals row then leads.
 */
export function traySummaryFrom(src: TraySources): TraySummary {
  const awaitingOrders = Math.max(
    src.ordersPending,
    src.orders.filter((o) => isAwaitingApproval(o)).length,
  )
  const awaitingMandates = src.mandates.filter((m) => m.status === 'awaiting_approval').length
  const tradeApprovals = awaitingOrders + awaitingMandates
  return {
    liveTurns: src.liveTurns,
    approvalsPending: src.toolApprovals + tradeApprovals,
    tradeApprovals,
    nextMandate: nextMandate(src.mandates),
  }
}

export function sameSummary(a: TraySummary, b: TraySummary): boolean {
  return (
    a.liveTurns === b.liveTurns &&
    a.approvalsPending === b.approvalsPending &&
    a.tradeApprovals === b.tradeApprovals &&
    a.nextMandate?.label === b.nextMandate?.label &&
    a.nextMandate?.at === b.nextMandate?.at
  )
}

export interface SummaryThrottle {
  push(summary: TraySummary): void
  cancel(): void
}

/**
 * At most one push per `intervalMs`: the first goes at once (main learns the
 * window is ready), a burst inside the window collapses to its last value,
 * sent when the window ends, and a value equal to the last one sent is not
 * sent again.
 */
export function createSummaryThrottle(
  send: (summary: TraySummary) => void,
  intervalMs = 1_000,
  now: () => number = Date.now,
): SummaryThrottle {
  let lastSent: TraySummary | null = null
  let lastAt = -Infinity
  let queued: TraySummary | null = null
  let timer: ReturnType<typeof setTimeout> | null = null

  const flush = () => {
    timer = null
    const next = queued
    queued = null
    if (!next || (lastSent && sameSummary(lastSent, next))) return
    lastSent = next
    lastAt = now()
    send(next)
  }

  return {
    push(summary) {
      queued = summary
      if (timer) return
      const wait = lastAt + intervalMs - now()
      if (wait <= 0) flush()
      else timer = setTimeout(flush, wait)
    },
    cancel() {
      if (timer) clearTimeout(timer)
      timer = null
      queued = null
    },
  }
}
