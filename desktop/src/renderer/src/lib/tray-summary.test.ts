import { afterEach, describe, expect, it, vi } from 'vitest'
import type { TraySummary } from '@shared/tray'
import type { Mandate } from '~/views/trading/types'
import { createSummaryThrottle, mandateLabel, nextMandate, traySummaryFrom } from './tray-summary'

function mandate(patch: {
  id?: string
  status?: Mandate['status']
  nextRunAt?: string | null
  name?: string
  token?: string
  quote?: string
}): Mandate {
  return {
    id: patch.id ?? 'm1',
    name: patch.name ?? 'DCA USDC',
    status: patch.status ?? 'active',
    token: { symbol: patch.token ?? 'USDC' },
    quote: { symbol: patch.quote ?? 'ETH' },
    schedule: { nextRunAt: patch.nextRunAt === undefined ? null : patch.nextRunAt },
  } as unknown as Mandate
}

const EMPTY_SOURCES = {
  liveTurns: 0,
  toolApprovals: 0,
  orders: [],
  ordersPending: 0,
  mandates: [],
}

describe('mandateLabel', () => {
  it('says what is spent, then what is bought', () => {
    expect(mandateLabel(mandate({ quote: 'ETH', token: 'USDC' }))).toBe('ETH → USDC')
  })

  it('falls back to the name when a symbol is missing', () => {
    expect(mandateLabel(mandate({ quote: '', token: 'USDC', name: 'Stack sats' }))).toBe(
      'Stack sats',
    )
  })
})

describe('nextMandate', () => {
  it('is the active mandate that buys first', () => {
    const next = nextMandate([
      mandate({ id: 'late', nextRunAt: '2026-10-03T15:00:00Z', quote: 'USDC', token: 'ETH' }),
      mandate({ id: 'soon', nextRunAt: '2026-10-03T12:14:00Z' }),
      mandate({ id: 'paused', status: 'paused', nextRunAt: '2026-10-03T12:01:00Z' }),
      mandate({ id: 'awaiting', status: 'awaiting_approval', nextRunAt: '2026-10-03T12:00:00Z' }),
    ])
    expect(next).toEqual({ label: 'ETH → USDC', at: '2026-10-03T12:14:00.000Z' })
  })

  it('is null without an active mandate with a next run', () => {
    expect(nextMandate([])).toBeNull()
    expect(nextMandate([mandate({ nextRunAt: null })])).toBeNull()
    expect(nextMandate([mandate({ nextRunAt: 'not a date' })])).toBeNull()
    expect(nextMandate([mandate({ status: 'paused', nextRunAt: '2026-10-03T12:00:00Z' })])).toBe(
      null,
    )
  })
})

describe('traySummaryFrom', () => {
  it('adds desk orders and mandates awaiting approval to the tool approvals', () => {
    const s = traySummaryFrom({
      ...EMPTY_SOURCES,
      liveTurns: 2,
      toolApprovals: 1,
      orders: [{ status: 'awaiting_approval' }, { status: 'confirmed' }],
      mandates: [mandate({ status: 'awaiting_approval' })],
    })
    expect(s).toEqual({
      liveTurns: 2,
      approvalsPending: 3,
      tradeApprovals: 2,
      nextMandate: null,
    })
  })

  it("trusts the engine's own count when the page holds fewer", () => {
    const s = traySummaryFrom({
      ...EMPTY_SOURCES,
      orders: [{ status: 'awaiting_approval' }],
      ordersPending: 25,
    })
    expect(s.tradeApprovals).toBe(25)
    expect(s.approvalsPending).toBe(25)
  })

  it('has nothing to say with nothing going on', () => {
    expect(traySummaryFrom(EMPTY_SOURCES)).toEqual({
      liveTurns: 0,
      approvalsPending: 0,
      tradeApprovals: 0,
      nextMandate: null,
    })
  })
})

describe('createSummaryThrottle', () => {
  afterEach(() => vi.useRealTimers())

  const s = (approvalsPending: number): TraySummary => ({
    liveTurns: 0,
    approvalsPending,
    tradeApprovals: 0,
    nextMandate: null,
  })

  it('sends the first at once, then at most one a second with the latest value', () => {
    vi.useFakeTimers()
    const send = vi.fn()
    const throttle = createSummaryThrottle(send, 1_000)
    throttle.push(s(0))
    expect(send).toHaveBeenCalledTimes(1)
    throttle.push(s(1))
    throttle.push(s(2))
    throttle.push(s(3))
    expect(send).toHaveBeenCalledTimes(1)
    vi.advanceTimersByTime(999)
    expect(send).toHaveBeenCalledTimes(1)
    vi.advanceTimersByTime(1)
    expect(send).toHaveBeenCalledTimes(2)
    expect(send).toHaveBeenLastCalledWith(s(3))
    // A quiet second later, the next change goes straight out.
    vi.advanceTimersByTime(5_000)
    throttle.push(s(4))
    expect(send).toHaveBeenCalledTimes(3)
  })

  it('does not resend what main already has', () => {
    vi.useFakeTimers()
    const send = vi.fn()
    const throttle = createSummaryThrottle(send, 1_000)
    throttle.push(s(1))
    throttle.push(s(2))
    throttle.push(s(1))
    vi.advanceTimersByTime(1_000)
    expect(send).toHaveBeenCalledTimes(1)
    vi.advanceTimersByTime(5_000)
    throttle.push(s(1))
    expect(send).toHaveBeenCalledTimes(1)
  })

  it('drops a queued push when cancelled', () => {
    vi.useFakeTimers()
    const send = vi.fn()
    const throttle = createSummaryThrottle(send, 1_000)
    throttle.push(s(0))
    throttle.push(s(1))
    throttle.cancel()
    vi.advanceTimersByTime(2_000)
    expect(send).toHaveBeenCalledTimes(1)
  })
})
