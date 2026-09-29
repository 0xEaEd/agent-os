import { readFileSync } from 'node:fs'
import { fireEvent, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { renderDesk, WALLET } from '../test-utils'
import type { Order } from '../types'
import { ApprovalCard } from './ApprovalCard'
import {
  approvalFacts,
  lpOrderLine,
  lpRangeStatus,
  lpStamps,
  orderKindWord,
  orderLine,
  rejectionMessage,
  riskStamp,
} from './desk-logic'
import { parseTradeCommand, parseTradeResult } from './ledger'

vi.mock('~/lib/desktop-api', () => ({
  desktopApi: () => ({ app: { openExternal: vi.fn() } }),
  isDesktop: () => true,
}))

// The order JSON the engine returns for each LP write (docs/lp-write.md,
// "Order model"): `_order_dict` plus `plan`.
const FIXTURES = 'src/renderer/src/views/trading/desk/__fixtures__/lp'

type LpKind = 'lp_collect' | 'lp_remove' | 'lp_add'

function raw(kind: LpKind): Record<string, unknown> {
  return JSON.parse(readFileSync(`${FIXTURES}/${kind}.json`, 'utf8')) as Record<string, unknown>
}

function lpOrder(kind: LpKind, extra: Partial<Order> = {}): Order {
  const now = Date.now()
  return { ...(raw(kind) as unknown as Order), expiresAt: now + 600_000, ...extra }
}

const LABELS = new Proxy({} as Record<string, string>, { get: (_t, key) => String(key) })

function card(o: Order) {
  renderDesk(
    <ApprovalCard
      order={o}
      wallets={[WALLET]}
      deciding={false}
      onApprove={vi.fn()}
      onReject={vi.fn()}
      focusOnMount={false}
    />,
  )
  return screen.getByTestId('approval-card')
}

function fact(label: string): string {
  const row = Array.from(document.querySelectorAll('.trd-card__fact')).find(
    (el) => el.querySelector('dt')?.textContent === label,
  )
  return row?.querySelector('dd')?.textContent ?? ''
}

describe('ApprovalCard · LP writes', () => {
  it('collect: title, position line, range with its pill, what comes back', () => {
    const el = card(lpOrder('lp_collect'))
    expect(el).toHaveAttribute('data-kind', 'lp_collect')
    expect(el).toHaveTextContent('Approval needed · Collect fees')
    expect(screen.getByTestId('card-lp-position')).toHaveTextContent(
      '#48213 · PEPE / WETH · Base · 1%',
    )
    // Market cap when both bounds are known.
    expect(screen.getByTestId('card-lp-range')).toHaveTextContent('$2.1M – $9.8M mcap')
    expect(screen.getByTestId('card-lp-status')).toHaveTextContent('in range')
    expect(fact('You receive')).toBe('1,240,000 PEPE + 0.00184 WETH · $15.93')
    expect(fact('Gas')).toBe('$0.02')
    expect(fact('Order')).toBe('lpo_c41a9e')
    expect(fact('Expires')).toMatch(/UTC[+−]/)
    // No slippage on a collect, no swap route, no amounts it does not move.
    expect(fact('Slippage')).toBe('')
    expect(fact('Route via')).toBe('')
    expect(fact('Pay')).toBe('')
    expect(screen.queryByTestId('stamp-lp-burns')).toBeNull()
    expect(screen.queryByTestId('risk-high')).toBeNull()
  })

  it('remove 100%: fees included, the minimum, and the burn stamp', () => {
    const el = card(lpOrder('lp_remove'))
    expect(el).toHaveTextContent('Approval needed · Remove liquidity')
    expect(screen.getByTestId('card-lp-position')).toHaveTextContent(
      '#48213 · PEPE / WETH · Base · 1%',
    )
    expect(screen.getByTestId('card-lp-position')).toHaveTextContent('100%')
    expect(fact('You receive')).toBe('61,240,000 PEPE + 0.294 WETH · $1,284.20')
    expect(fact('Fees included')).toBe('1,240,000 PEPE + 0.00184 WETH · $15.93')
    expect(fact('Minimum')).toBe('60,627,600 PEPE + 0.29106 WETH')
    expect(fact('Slippage')).toBe('1.00%')
    expect(screen.getByTestId('stamp-lp-burns')).toHaveTextContent('Burns the position NFT')
    // $1,284 is over the high-risk line: approve arms first.
    expect(screen.getByTestId('risk-high')).toBeInTheDocument()
  })

  it('remove 50%: no burn stamp', () => {
    const o = lpOrder('lp_remove')
    card({ ...o, plan: { ...o.plan!, pct: 50 } })
    expect(screen.getByTestId('card-lp-position')).toHaveTextContent('50%')
    expect(screen.queryByTestId('stamp-lp-burns')).toBeNull()
  })

  it('add: a new position, one-sided, the deposit, its maximum and the approvals', () => {
    const el = card(lpOrder('lp_add'))
    expect(el).toHaveTextContent('Approval needed · Add liquidity')
    expect(screen.getByTestId('card-lp-position')).toHaveTextContent(
      'new position · PEPE / WETH · Base · 1%',
    )
    // No mcap bounds: the range is quoted as a price.
    expect(screen.getByTestId('card-lp-range')).toHaveTextContent(/WETH per PEPE/)
    // Pool tick below the range, base = currency1: PEPE is priced above it.
    expect(screen.getByTestId('card-lp-status')).toHaveTextContent('above range')
    expect(fact('You deposit')).toBe('0.020404 WETH · $50.00')
    expect(fact('Maximum')).toBe('0.02060804 WETH')
    expect(fact('Approvals needed')).toBe('WETH → Permit2, WETH → PositionManager')
    expect(screen.getByTestId('stamp-lp-oneSided')).toHaveTextContent(
      'One-sided: all WETH until price enters the range',
    )
    expect(screen.queryByTestId('stamp-lp-hook')).toBeNull()
  })

  it('add into a pool with a hook says so, and names the hook on hover', () => {
    const o = lpOrder('lp_add')
    const hooks = '0x1234567890abcdef1234567890abcdef12345678'
    const plan = {
      ...o.plan!,
      pool: { ...o.plan!.pool, poolKey: { ...o.plan!.pool.poolKey!, hooks } },
    }
    card({ ...o, plan })
    const stamp = screen.getByTestId('stamp-lp-hook')
    expect(stamp).toHaveTextContent('Pool has a hook')
    expect(stamp).toHaveAttribute('title', hooks)
  })

  it('add with nothing to approve says "none"', () => {
    const o = lpOrder('lp_add')
    card({ ...o, plan: { ...o.plan!, approvals: [] } })
    expect(fact('Approvals needed')).toBe('none')
  })

  it('a settled LP card is a receipt: what the receipt moved, no bounds, a tx link', () => {
    const o = lpOrder('lp_collect', {
      status: 'confirmed',
      txHash: `0x${'ab'.repeat(32)}`,
      explorerUrl: `https://basescan.org/tx/0x${'ab'.repeat(32)}`,
      received: {
        base: { raw: '1250000000000000000000000', human: '1250000', usd: 11.5 },
        quote: { raw: '1850000000000000', human: '0.00185', usd: 4.53 },
      },
    })
    const el = card(o)
    expect(el).toHaveTextContent('Collect fees')
    expect(el).not.toHaveTextContent('Approval needed')
    expect(fact('You receive')).toBe('1,250,000 PEPE + 0.00185 WETH')
    expect(screen.queryByTestId('card-approve')).toBeNull()
    expect(el.querySelector('.trd-card__link')).not.toBeNull()
  })

  it('approves a normal LP ask in one click', () => {
    const onApprove = vi.fn()
    renderDesk(
      <ApprovalCard
        order={lpOrder('lp_collect')}
        wallets={[WALLET]}
        deciding={false}
        onApprove={onApprove}
        onReject={vi.fn()}
        focusOnMount={false}
      />,
    )
    fireEvent.click(screen.getByTestId('card-approve'))
    expect(onApprove).toHaveBeenCalledTimes(1)
  })
})

describe('desk-logic · LP writes', () => {
  it('reads in/out of range from the ticks and which side the base is on', () => {
    const plan = lpOrder('lp_collect').plan!
    expect(lpRangeStatus(plan)).toBe('in-range')
    // At tickUpper the position is out (V4: lower <= tick < upper).
    const at = (tick: number, base = plan.token.address) =>
      lpRangeStatus({
        ...plan,
        pool: { ...plan.pool, tick },
        token: { ...plan.token, address: base },
      })
    expect(at(-180000)).toBe('below-range') // base = currency1: a higher tick is a lower price
    expect(at(-210000)).toBe('above-range')
    expect(at(-180000, plan.pool.poolKey!.currency0)).toBe('above-range')
    expect(at(-210000, plan.pool.poolKey!.currency0)).toBe('below-range')
    // The engine's word wins when it gives one.
    expect(lpRangeStatus({ ...plan, status: 'below-range' })).toBe('below-range')
  })

  it('names LP orders in toasts, notifications and rejection messages', () => {
    const collect = lpOrder('lp_collect')
    const remove = lpOrder('lp_remove')
    const add = lpOrder('lp_add')
    expect(orderKindWord(collect)).toBe('lp_collect')
    expect(orderLine(collect)).toBe('#48213 · PEPE / WETH')
    expect(orderLine(remove)).toBe('#48213 · 100% · PEPE / WETH')
    expect(lpOrderLine(add)).toBe('PEPE / WETH · $50.00')
    expect(lpOrderLine({ ...add, plan: { ...add.plan!, tokenId: '9', increase: true } })).toBe(
      'PEPE / WETH · $50.00 · → #9',
    )
    expect(rejectionMessage(collect, 'not now')).toBe(
      'Rejected lp collect order lpo_c41a9e: not now',
    )
  })

  it('stamps only what applies', () => {
    expect(lpStamps(lpOrder('lp_collect'))).toEqual([])
    expect(lpStamps(lpOrder('lp_remove'))).toEqual([{ key: 'burns' }])
    expect(lpStamps(lpOrder('lp_add'))).toEqual([{ key: 'oneSided', token: 'WETH' }])
  })

  it('collect with nothing owed: a "this only pays gas" stamp; unknown fees are not nothing', () => {
    const o = lpOrder('lp_collect')
    const zeroSide = { raw: '0', human: '0', usd: 0 }
    const plan = o.plan!
    // $0 exactly.
    expect(lpStamps({ ...o, plan: { ...plan, fees: { ...plan.fees!, usd: 0 } } })).toEqual([
      { key: 'noFees' },
    ])
    // Both raw sides "0", unpriced.
    const empty = { base: zeroSide, quote: zeroSide, usd: null }
    expect(lpStamps({ ...o, plan: { ...plan, fees: empty } })).toEqual([{ key: 'noFees' }])
    // One side still owes: no stamp.
    expect(
      lpStamps({ ...o, plan: { ...plan, fees: { ...plan.fees!, base: zeroSide, usd: null } } }),
    ).toEqual([])
    // Only a collect: a remove with no fees still returns the principal.
    expect(
      lpStamps({ ...lpOrder('lp_remove'), plan: { ...lpOrder('lp_remove').plan!, fees: empty } }),
    ).toEqual([{ key: 'burns' }])

    card({ ...o, plan: { ...plan, fees: empty, expected: empty } })
    expect(screen.getByTestId('stamp-lp-noFees')).toHaveTextContent(
      'no fees to collect — this only pays gas',
    )
  })

  it('an unpriced deposit is high risk; an unpriced collect is not', () => {
    expect(riskStamp({ ...lpOrder('lp_add'), valueUsd: null })).toBe('high')
    expect(riskStamp({ ...lpOrder('lp_collect'), valueUsd: null })).toBe('normal')
  })

  it('lists the LP facts in order, gas and order last', () => {
    const keys = approvalFacts(lpOrder('lp_remove'), [WALLET], LABELS).map((f) => f.key)
    expect(keys).toEqual([
      'wallet',
      'chain',
      'lpReceive',
      'lpFees',
      'lpMinimum',
      'lpSlippage',
      'gas',
      'order',
      'expires',
    ])
  })
})

describe('ledger · LP writes', () => {
  it('gives each write its own kind and label, from the command line', () => {
    const collect = parseTradeCommand('agentos trade lp collect 48213 --chain base --json')!
    expect(collect.kind).toBe('lp_collect')
    expect(collect.title).toBe('Collect fees')
    expect(collect.detail).toBe('#48213 · Base')

    const remove = parseTradeCommand('agentos trade lp remove 48213 --chain base --json')!
    expect(remove.kind).toBe('lp_remove')
    expect(remove.title).toBe('Remove liquidity')
    expect(remove.detail).toBe('#48213 · 100% · Base')
    expect(parseTradeCommand('agentos trade lp remove --wait 48213 --pct 50')!.detail).toBe(
      '#48213 · 50%',
    )

    const add = parseTradeCommand(
      'agentos trade lp add PEPE --chain base --usd 50 --range mcap:2M-10M --json',
    )!
    expect(add.kind).toBe('lp_add')
    expect(add.title).toBe('Add liquidity')
    expect(add.detail).toBe('PEPE · $50 · Base')
    expect(
      parseTradeCommand('agentos trade lp add PEPE --amount-base 1000 --to-position 48213')!.detail,
    ).toBe('PEPE · 1000 PEPE · → #48213')

    // The reads stay one "Liquidity read" kind.
    const read = parseTradeCommand('agentos trade lp positions --json')!
    expect(read.kind).toBe('lp')
    expect(read.title).toBe('Liquidity read')
  })

  it('reads the order JSON: awaiting approval, then the order names the subject', () => {
    const call = parseTradeCommand('agentos trade lp collect 48213 --chain base --json')!
    const out = parseTradeResult(call, `exit_code=0\n${JSON.stringify(raw('lp_collect'))}`)
    expect(out.awaiting).toBe(true)
    expect(out.orderId).toBe('lpo_c41a9e')
    expect(out.detail).toBe('#48213 · Base')
    expect(out.summary).toBe('receive 1,240,000 PEPE + 0.00184 WETH · awaiting approval')
    // An LP write is no swap route: no provider logo on the row.
    expect(out.provider).toBeNull()

    const addCall = parseTradeCommand('agentos trade lp add PEPE --usd 50 --chain base --json')!
    const add = parseTradeResult(addCall, JSON.stringify({ order: raw('lp_add') }))
    expect(add.detail).toBe('PEPE/WETH · $50.00 · Base')
    expect(add.summary).toBe('deposit 0.020404 WETH · awaiting approval')

    const removeCall = parseTradeCommand('agentos trade lp remove 48213 --chain base')!
    const remove = parseTradeResult(removeCall, JSON.stringify({ orders: [raw('lp_remove')] }))
    expect(remove.detail).toBe('#48213 · 100% · Base')
  })

  it('reports confirmed, rejected and failed as for a send', () => {
    const call = parseTradeCommand('agentos trade lp remove 48213 --chain base --wait --json')!
    const tx = `0x${'cd'.repeat(32)}`
    const confirmed = parseTradeResult(
      call,
      `${JSON.stringify({
        ...raw('lp_remove'),
        status: 'confirmed',
        txHash: tx,
        explorerUrl: `https://basescan.org/tx/${tx}`,
        received: {
          base: { raw: '1', human: '61000000', usd: null },
          quote: { raw: '1', human: '0.29', usd: null },
        },
      })}\npublish_artifact path=lp-cards/positions-x.json mime=application/vnd.agentos.lp+json`,
    )
    expect(confirmed.confirmed).toBe(true)
    expect(confirmed.txHash).toBe(tx)
    expect(confirmed.summary).toBe('receive 61,000,000 PEPE + 0.29 WETH · confirmed')

    const rejected = parseTradeResult(
      call,
      JSON.stringify({ ...raw('lp_remove'), status: 'rejected', reason: 'not now' }),
    )
    expect(rejected.summary).toMatch(/· rejected · not now$/)
    expect(rejected.error).toBeNull()

    const failed = parseTradeResult(
      call,
      JSON.stringify({ ...raw('lp_remove'), status: 'failed', reason: 'price moved' }),
    )
    expect(failed.error).toBe('price moved')

    const refused = parseTradeResult(
      call,
      'exit_code=1\n{"error":{"code":"trading.lp.not_owner","message":"position 48213 is not in the vault"}}',
    )
    expect(refused.error).toBe('position 48213 is not in the vault')
  })
})

describe('ledger · LP writes, result truncated', () => {
  // The stored tool result stops at ~2,000 characters; an LP order is 3–4 KB,
  // so the JSON never parses. The engine prints `{"order": {…}}` with `plan`,
  // `tokenId`, `received`, `spent` last (`_order_dict` + `_lp_order_fields`).
  const CUT = 2000
  const tx = `0x${'a9'.repeat(32)}`
  const cut = (value: unknown, prefix = 'exit_code=0\n'): string => {
    const text = `${prefix}${JSON.stringify(value)}`
    expect(text.length).toBeGreaterThan(CUT)
    const sliced = text.slice(0, CUT)
    expect(() => JSON.parse(sliced.slice(prefix.length))).toThrow()
    return sliced
  }
  const settled = (kind: LpKind, extra: Record<string, unknown>): Record<string, unknown> => ({
    ...raw(kind),
    tokenId: '48213',
    received: {
      base: { raw: '1', human: '61000000', usd: null },
      quote: { raw: '1', human: '0.29', usd: null },
    },
    spent: {
      base: { raw: '0', human: '0', usd: 0 },
      quote: { raw: '0', human: '0', usd: 0 },
    },
    ...extra,
  })
  const removeCall = parseTradeCommand('agentos trade lp remove 48213 --chain base --wait --json')!

  it('reads a confirmed remove whose receipt was cut off: status and tx, never JSON', () => {
    const text = cut({
      order: settled('lp_remove', {
        status: 'confirmed',
        txHash: tx,
        explorerUrl: `https://basescan.org/tx/${tx}`,
      }),
    })
    expect(text.startsWith('exit_code=0\n{"order":{"orderId":"lpo_r7d02b"')).toBe(true)
    const out = parseTradeResult(removeCall, text)
    expect(out.summary).toBe('confirmed')
    expect(out.confirmed).toBe(true)
    expect(out.txHash).toBe(tx)
    expect(out.explorerUrl).toBe(`https://basescan.org/tx/${tx}`)
    expect(out.orderId).toBe('lpo_r7d02b')
    expect(out.provider).toBeNull()
    expect(out.error).toBeNull()
    // The command line still names the subject.
    expect(out.detail).toBeUndefined()
  })

  it('names the received amounts when the cut fell after them', () => {
    // A receipt that lands before the cut: the same fields, ahead of `plan`.
    const { plan, ...order } = settled('lp_remove', {
      status: 'confirmed',
      txHash: tx,
      tokenIn: { ...(raw('lp_remove').tokenIn as object), symbol: 'ETH' },
      tokenOut: { ...(raw('lp_remove').tokenOut as object), symbol: 'USDC' },
      received: {
        base: { raw: '18834597061965', human: '0.000018834597061965', usd: 0.0498 },
        quote: { raw: '0', human: '0', usd: 0 },
      },
    })
    const out = parseTradeResult(removeCall, cut({ order: { ...order, plan } }, ''))
    expect(out.summary).toBe('receive 0.00001883 ETH · confirmed')
    expect(out.confirmed).toBe(true)
  })

  it('reads a collect and an add still awaiting approval: the order id to jump to', () => {
    const collect = parseTradeResult(
      parseTradeCommand('agentos trade lp collect 48213 --chain base --json')!,
      cut({ order: raw('lp_collect') }),
    )
    expect(collect.summary).toBe('awaiting approval · #lpo_c41a9e')
    expect(collect.awaiting).toBe(true)
    expect(collect.orderId).toBe('lpo_c41a9e')

    // Pretty-printed and bare works the same.
    const addText = JSON.stringify(raw('lp_add'), null, 2).slice(0, CUT)
    const add = parseTradeResult(
      parseTradeCommand('agentos trade lp add PEPE --usd 50 --chain base --json')!,
      addText,
    )
    expect(add.summary).toBe('awaiting approval · #lpo_a90c3f')
    expect(add.awaiting).toBe(true)
  })

  it('reads a confirmed add, rejected and failed writes like the parsed path', () => {
    const add = parseTradeResult(
      parseTradeCommand('agentos trade lp add PEPE --usd 50 --chain base --json')!,
      cut({ order: settled('lp_add', { status: 'confirmed', txHash: tx }) }),
    )
    expect(add.summary).toBe('confirmed')
    expect(add.confirmed).toBe(true)

    const rejected = parseTradeResult(
      removeCall,
      cut({ order: settled('lp_remove', { status: 'rejected', reason: 'user: not now' }) }),
    )
    expect(rejected.summary).toBe('rejected · user: not now')
    expect(rejected.error).toBeNull()
    expect(rejected.awaiting).toBe(false)

    const failed = parseTradeResult(
      removeCall,
      cut({ order: settled('lp_remove', { status: 'failed', reason: 'price moved' }) }),
    )
    expect(failed.summary).toBe('failed · price moved')
    expect(failed.error).toBe('price moved')
    expect(failed.confirmed).toBe(false)
  })

  it('says the result was truncated when nothing can be read, and keeps plain error lines', () => {
    const none = parseTradeResult(removeCall, 'exit_code=0\n{"order": {"recipient": "0x7A3f9C21b4')
    expect(none.summary).toBe('result truncated')
    expect(none.summary).not.toContain('{')

    const error = parseTradeResult(
      removeCall,
      'exit_code=1\n{"error": {"code": "trading.lp.not_owner", "message": "position 48213 is not in the vault", "details": {"tok',
    )
    expect(error.error).toBe('position 48213 is not in the vault')

    const plain = parseTradeResult(removeCall, 'exit_code=2\nclientOrderId must be 1-64 characters')
    expect(plain.summary).toBe('clientOrderId must be 1-64 characters')
    expect(plain.error).toBe('clientOrderId must be 1-64 characters')
  })
})
