import { readFileSync } from 'node:fs'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, renderHook, screen, waitFor, within } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useConnection } from '@/stores/connection'
import { useTouchIdPrompt } from '~/lib/biometric-gate'
import { order, renderDesk, WALLET } from '../test-utils'
import type { MandatePayload, Trigger, TriggerFire, TriggerPayload } from '../types'
import { ApprovalsRegion } from './ApprovalsRegion'
import { MissionControls, MissionStrip } from './MissionControls'
import { useMissions } from './missions'
import { StatusStrip } from './StatusStrip'
import { TriggerCard } from './TriggerCard'
import {
  deskTriggers,
  distanceText,
  heroText,
  isNear,
  nowText,
  planText,
  priceText,
  sizeText,
  triggerChip,
  triggerChipText,
  triggerRows,
  triggerWord,
} from './trigger-logic'

const rpcCall = vi.fn()
vi.mock('@/app/providers', () => ({
  useRpc: () => ({ call: rpcCall, waitForConnection: async () => {}, on: () => () => {} }),
}))
const toasts = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}))
vi.mock('sonner', () => ({ toast: toasts }))

const PAYLOAD = JSON.parse(
  readFileSync('src/renderer/src/views/trading/desk/__fixtures__/trigger/trigger.json', 'utf8'),
) as TriggerPayload
const MANDATE = (
  JSON.parse(
    readFileSync('src/renderer/src/views/trading/desk/__fixtures__/dca/mandate.json', 'utf8'),
  ) as MandatePayload
).mandate
const SESSION = 'agent:trading:webchat:desk-test'
const NOW = Date.parse('2026-10-04T06:00:00Z')

function trigger(extra: Partial<Trigger> = {}): Trigger {
  return { ...PAYLOAD.trigger, ...extra }
}

/** The fixture, armed and 0.3 % above its line. */
function near(extra: Partial<Trigger> = {}): Trigger {
  return trigger({
    market: { ...PAYLOAD.trigger.market, priceUsd: 3811.4, distancePct: -0.3 },
    ...extra,
  })
}

const PENDING = trigger({
  id: 'trg_99',
  status: 'awaiting_approval',
  approvedAt: null,
  armedAt: null,
  fires: [],
  expiresAt: '2026-10-05T08:00:00Z',
})

const BUY = trigger({
  id: 'trg_buy',
  name: 'Buy ETH under $3,500',
  kind: 'buy',
  condition: { ...PAYLOAD.trigger.condition, priceUsd: 3500, label: 'under $3,500' },
  action: {
    ...PAYLOAD.trigger.action,
    kind: 'buy',
    amountPct: null,
    amountUsd: 50,
    estimatedUsd: 50,
    needsApproval: false,
    label: 'buy $50 of ETH with USDC',
  },
})

const ALERT = trigger({
  id: 'trg_al',
  name: 'Alert ETH over $5,000',
  kind: 'alert',
  condition: {
    ...PAYLOAD.trigger.condition,
    direction: 'above',
    priceUsd: 5000,
    label: 'over $5,000',
  },
  action: {
    ...PAYLOAD.trigger.action,
    kind: 'alert',
    amountPct: null,
    estimatedUsd: null,
    needsApproval: false,
    label: 'notify',
  },
  market: { ...PAYLOAD.trigger.market, balance: null, distancePct: 28.8 },
})

describe('trigger logic', () => {
  it('says prices and distances the way a trader reads them', () => {
    expect(priceText(3790)).toBe('$3,790')
    expect(priceText(3881.6)).toBe('$3,882')
    expect(priceText(1.25)).toBe('$1.25')
    expect(priceText(0.9998)).toBe('$0.9998')
    expect(priceText(0.00001234)).toBe('$0.00001234')
    expect(priceText(null)).toBe('—')
    expect(distanceText(-2.1)).toBe('−2.1 %')
    expect(distanceText(4)).toBe('+4.0 %')
    expect(distanceText(0)).toBe('0 %')
    expect(distanceText(-0.05)).toBe('−0.05 %')
    expect(distanceText(null)).toBe('')
  })

  it('reads the sentence, the size and the plan off the engine figures', () => {
    expect(heroText(trigger())).toBe('sell 50 % of ETH when under $3,800')
    expect(heroText(BUY)).toBe('buy $50 of ETH when under $3,500')
    expect(heroText(ALERT)).toBe('notify when ETH is over $5,000')
    const fixed = trigger({
      action: {
        ...PAYLOAD.trigger.action,
        amountPct: null,
        amount: { raw: '50000000000000000', human: '0.05', usd: 194 },
      },
    })
    expect(heroText(fixed)).toBe('sell 0.05 ETH when under $3,800')
    expect(sizeText(trigger())).toBe('50 % · ≈ $194')
    expect(sizeText(fixed)).toBe('0.05 ETH · ≈ $194')
    expect(sizeText(BUY)).toBe('$50')
    expect(sizeText(ALERT)).toBe('—')
    expect(planText(trigger())).toBe('sell 50 % ETH · under $3,800')
    expect(planText(ALERT)).toBe('ETH over $5,000')
    // A trail reads its own label.
    const trail = trigger({
      condition: {
        ...PAYLOAD.trigger.condition,
        direction: 'trail',
        priceUsd: null,
        trailPct: 10,
        label: '',
      },
    })
    expect(heroText(trail)).toBe('sell 50 % of ETH when 10 % below peak')
  })

  it('says each state in the contract’s words', () => {
    expect(triggerWord(trigger())).toBe('Armed · ETH $3,882 · −2.1 %')
    expect(triggerWord(near())).toBe('Armed · ETH $3,811 · −0.3 %')
    expect(triggerWord(trigger({ condition: { ...PAYLOAD.trigger.condition, hits: 1 } }))).toBe(
      'Armed · 1 of 2 checks',
    )
    expect(
      triggerWord(
        trigger({ market: { ...PAYLOAD.trigger.market, priceUsd: null, distancePct: null } }),
      ),
    ).toBe('Armed · waiting for a price')
    expect(triggerWord(trigger({ status: 'triggered' }))).toBe('Triggered · order open')
    expect(triggerWord(trigger({ status: 'paused' }))).toBe('Paused')
    expect(triggerWord(trigger({ status: 'done' }))).toBe('Done')
    expect(triggerWord(PENDING)).toBe('Awaiting approval')
  })

  it('says the live line under the sentence', () => {
    expect(nowText(trigger())).toBe('ETH $3,882 · 2.1 % above the line')
    expect(nowText(BUY)).toBe('ETH $3,882 · 2.1 % above the line')
    expect(nowText(ALERT)).toBe('ETH $3,882 · 28.8 % below the line')
    expect(nowText(trigger({ condition: { ...PAYLOAD.trigger.condition, hits: 1 } }))).toBe(
      'ETH $3,882 · fires after 1 more check',
    )
    expect(nowText(trigger({ market: { ...PAYLOAD.trigger.market, priceUsd: null } }))).toBe(
      'armed, waiting for a price',
    )
    expect(nowText(PENDING)).toBe('ETH $3,882 · awaiting approval')
  })

  it('is near within 1 % of the line, and only while armed', () => {
    expect(isNear(near())).toBe(true)
    expect(isNear(trigger())).toBe(false)
    expect(isNear(near({ status: 'paused' }))).toBe(false)
    expect(isNear(trigger({ market: { ...PAYLOAD.trigger.market, distancePct: 0 } }))).toBe(true)
  })

  it('lists this chat’s triggers and unfiled ones; every finished one for an hour only', () => {
    const list = [
      trigger(),
      trigger({ id: 'other', sessionKey: 'agent:trading:webchat:other' }),
      trigger({ id: 'unfiled', sessionKey: null }),
      trigger({ id: 'old', status: 'done', updatedAt: '2026-10-04T04:30:00Z' }),
      trigger({ id: 'fresh', status: 'stopped', updatedAt: '2026-10-04T05:40:00Z' }),
    ]
    expect(deskTriggers(list, SESSION, NOW).map((tr) => tr.id)).toEqual([
      'trg_1a2b3c4d',
      'unfiled',
      'fresh',
    ])
    const finished = ['a', 'b', 'c'].map((id) => trigger({ id, status: 'expired' }))
    const { rows, more } = triggerRows([trigger(), ...finished])
    expect(rows.map((tr) => tr.id)).toEqual(['trg_1a2b3c4d', 'a', 'b'])
    expect(more).toBe(1)
  })

  it('sums the live triggers in one chip: fired, near, awaiting, else armed', () => {
    const text = (list: Trigger[]) => {
      const chip = triggerChip(list)
      return chip ? triggerChipText(chip) : null
    }
    expect(text([trigger()])).toBe('Trigger · armed')
    expect(text([trigger(), BUY])).toBe('Triggers ×2')
    expect(text([near()])).toBe('Trigger · near')
    expect(text([trigger({ status: 'triggered' })])).toBe('Trigger · fired')
    expect(text([trigger(), PENDING])).toBe('Triggers ×2 · awaiting')
    expect(text([trigger({ status: 'paused' })])).toBe('Trigger · paused')
    expect(text([trigger({ status: 'done' })])).toBeNull()
    expect(triggerChip([near(), trigger({ status: 'triggered' })])?.word).toBe('fired')
  })
})

describe('TriggerCard', () => {
  afterEach(() => useTouchIdPrompt.setState({ key: null }))

  it('says the whole trigger before the one button that arms it', () => {
    const onApprove = vi.fn()
    const onReject = vi.fn()
    renderDesk(
      <TriggerCard
        trigger={PENDING}
        wallets={[WALLET]}
        deciding={false}
        onApprove={onApprove}
        onReject={onReject}
      />,
    )
    const card = screen.getByTestId('trigger-card')
    expect(card).toHaveAttribute('data-status', 'awaiting_approval')
    expect(card).toHaveAttribute('data-action', 'sell')
    expect(card).toHaveTextContent('Stop-loss ETH')
    expect(screen.getByTestId('trigger-hero')).toHaveTextContent(
      'ETHsell 50 % of ETH when under $3,800',
    )
    expect(screen.getByTestId('trigger-now')).toHaveTextContent('ETH $3,882 · awaiting approval')
    const fact = (key: string) => card.querySelector(`[data-fact='${key}'] dd`)?.textContent
    expect(fact('what')).toBe('sell 50 % of ETH')
    expect(fact('when')).toBe('under $3,800')
    expect(fact('size')).toBe('50 % · ≈ $194')
    expect(fact('now')).toBe('$3,882')
    expect(fact('balance')).toBe('0.1 ETH')
    expect(fact('wallet')).toBe('Main · 0x1111…1111')
    expect(fact('chain')).toBe('Base')
    expect(fact('validUntil')).toBe('until stopped')
    expect(fact('approval')).toBe('waits for you · over $100')
    expect(fact('expires')).toMatch(/^Oct (4|5) \d{2}:\d{2}$/)
    expect(screen.getByTestId('trigger-needs-approval')).toBeInTheDocument()
    expect(screen.getByTestId('trigger-warnings')).toHaveTextContent(
      'a sell of ≈$194 is above the $100 approval threshold and will wait for you when it fires',
    )
    expect(screen.getByTestId('trigger-approve')).toHaveTextContent('Approve & arm')
    fireEvent.click(screen.getByTestId('trigger-approve'))
    expect(onApprove).toHaveBeenCalledWith(PENDING)
    fireEvent.click(screen.getByTestId('trigger-reject'))
    expect(onReject).toHaveBeenCalledWith(PENDING)
  })

  it('warns about a line already crossed, nothing to sell, and no price', () => {
    const { unmount } = renderDesk(
      <TriggerCard
        trigger={trigger({
          status: 'awaiting_approval',
          market: {
            ...PAYLOAD.trigger.market,
            priceUsd: 3700,
            distancePct: 0,
            balance: { raw: '0', human: '0', usd: 0 },
          },
        })}
        wallets={[WALLET]}
        deciding={false}
        onApprove={vi.fn()}
        onReject={vi.fn()}
      />,
    )
    const warnings = screen.getByTestId('trigger-warnings')
    expect(warnings).toHaveTextContent(
      'ETH is already at $3,700, under $3,800: this fires after the next two checks',
    )
    expect(warnings).toHaveTextContent('Main holds no ETH')
    unmount()
    renderDesk(
      <TriggerCard
        trigger={{
          ...ALERT,
          status: 'awaiting_approval',
          market: { ...ALERT.market, priceUsd: null, distancePct: null },
        }}
        wallets={[WALLET]}
        deciding={false}
        onApprove={vi.fn()}
        onReject={vi.fn()}
      />,
    )
    expect(screen.getByTestId('trigger-warnings')).toHaveTextContent(
      'price unknown: the trigger waits until ETH has a price',
    )
    // An alert trades nothing: no size, no balance, no approval line.
    const card = screen.getByTestId('trigger-card')
    expect(card.querySelector(`[data-fact='size'] dd`)).toHaveTextContent(
      'a notification, no order',
    )
    expect(card.querySelector(`[data-fact='approval']`)).toBeNull()
    expect(card.querySelector(`[data-fact='balance']`)).toBeNull()
  })

  it('locks both buttons while the decision is in flight, and says Touch ID while it asks', () => {
    const { unmount } = renderDesk(
      <TriggerCard
        trigger={PENDING}
        wallets={[WALLET]}
        deciding
        onApprove={vi.fn()}
        onReject={vi.fn()}
      />,
    )
    expect(screen.getByTestId('trigger-approve')).toBeDisabled()
    expect(screen.getByTestId('trigger-reject')).toBeDisabled()
    unmount()
    useTouchIdPrompt.setState({ key: `trigger:${PENDING.id}` })
    renderDesk(
      <TriggerCard
        trigger={PENDING}
        wallets={[WALLET]}
        deciding={false}
        onApprove={vi.fn()}
        onReject={vi.fn()}
      />,
    )
    expect(screen.getByTestId('trigger-approve')).toHaveTextContent('Touch ID…')
    expect(screen.getByTestId('trigger-approve')).toBeDisabled()
    expect(screen.getByTestId('trigger-reject')).not.toBeDisabled()
  })

  it('docks above the pending mandates and orders, and takes focus on Reject once', () => {
    const onApproveTrigger = vi.fn()
    const onRejectTrigger = vi.fn()
    renderDesk(
      <ApprovalsRegion
        pending={[]}
        settled={[]}
        wallets={[WALLET]}
        deciding={null}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        focusOrderId={null}
        mandates={[{ ...MANDATE, id: 'dca_99', status: 'awaiting_approval' }]}
        triggers={[PENDING]}
        onApproveTrigger={onApproveTrigger}
        onRejectTrigger={onRejectTrigger}
      />,
    )
    const region = screen.getByTestId('approvals-region')
    const cards = [...region.querySelectorAll('article')]
    expect(cards[0]).toHaveAttribute('data-testid', 'trigger-card')
    expect(cards[1]).toHaveAttribute('data-testid', 'mandate-card')
    expect(document.activeElement).toBe(
      within(cards[0] as HTMLElement).getByTestId('trigger-reject'),
    )
    fireEvent.click(within(cards[0] as HTMLElement).getByTestId('trigger-approve'))
    expect(onApproveTrigger).toHaveBeenCalledWith(PENDING)
    fireEvent.click(within(cards[0] as HTMLElement).getByTestId('trigger-reject'))
    expect(onRejectTrigger).toHaveBeenCalledWith(PENDING)
  })

  it('leaves the focus to a pending order, and renders for a pending trigger alone', () => {
    const { unmount } = renderDesk(
      <ApprovalsRegion
        pending={[order({ orderId: 'o1', status: 'awaiting_approval', initiator: 'agent' })]}
        settled={[]}
        wallets={[WALLET]}
        deciding={null}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        focusOrderId={null}
        triggers={[PENDING]}
      />,
    )
    const cards = [...screen.getByTestId('approvals-region').querySelectorAll('article')]
    expect(cards.map((c) => c.getAttribute('data-testid'))).toEqual([
      'trigger-card',
      'approval-card',
    ])
    expect(document.activeElement).not.toBe(screen.getByTestId('trigger-reject'))
    unmount()
    renderDesk(
      <ApprovalsRegion
        pending={[]}
        settled={[]}
        wallets={[WALLET]}
        deciding={null}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        focusOrderId={null}
        triggers={[PENDING]}
      />,
    )
    expect(screen.getByTestId('trigger-card')).toBeInTheDocument()
  })
})

describe('triggers among the missions', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(NOW)
  })
  afterEach(() => vi.useRealTimers())

  function controls(triggers: Trigger[], extra: Record<string, unknown> = {}) {
    const handlers = {
      onTriggerPause: vi.fn(),
      onTriggerResume: vi.fn(),
      onTriggerFire: vi.fn(),
      onTriggerStop: vi.fn(),
    }
    const view = renderDesk(
      <MissionControls
        missions={[]}
        running={new Set()}
        pendingApprovals={0}
        busy={false}
        onStart={vi.fn()}
        onEdit={vi.fn()}
        onRun={vi.fn()}
        onSetEnabled={vi.fn()}
        onRemove={vi.fn()}
        showStart={false}
        triggers={triggers}
        {...handlers}
        {...extra}
      />,
    )
    return { ...handlers, view }
  }

  it('shows an armed trigger with its price, distance and plan, and Pause · Sell now · Stop', () => {
    const h = controls([trigger()])
    const row = screen.getByTestId('trigger-row')
    expect(row).toHaveAttribute('data-state', 'armed')
    expect(row).toHaveAttribute('data-action', 'sell')
    expect(row).not.toHaveAttribute('data-near')
    expect(row).toHaveTextContent('Stop-loss ETH')
    expect(screen.getByTestId('trigger-word')).toHaveTextContent('Armed · ETH $3,882 · −2.1 %')
    expect(screen.getByTestId('trigger-plan')).toHaveTextContent('sell 50 % ETH · under $3,800')
    expect(screen.queryByTestId('trigger-resume')).toBeNull()
    expect(screen.getByTestId('trigger-fire')).toHaveAttribute('title', 'Sell now')
    fireEvent.click(screen.getByTestId('trigger-pause'))
    expect(h.onTriggerPause).toHaveBeenCalledTimes(1)
  })

  it('asks for a second click before Fire now and before Stop, inline', () => {
    const confirm = vi.spyOn(window, 'confirm')
    const h = controls([trigger()])
    fireEvent.click(screen.getByTestId('trigger-fire'))
    expect(h.onTriggerFire).not.toHaveBeenCalled()
    expect(screen.getByTestId('trigger-fire')).toHaveTextContent('Sell now — click again')
    fireEvent.click(screen.getByTestId('trigger-fire'))
    expect(h.onTriggerFire).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByTestId('trigger-stop'))
    expect(h.onTriggerStop).not.toHaveBeenCalled()
    expect(screen.getByTestId('trigger-stop')).toHaveTextContent('Stop — click again')
    fireEvent.click(screen.getByTestId('trigger-stop'))
    expect(h.onTriggerStop).toHaveBeenCalledTimes(1)
    expect(confirm).not.toHaveBeenCalled()
  })

  it('arms one two-click control at a time', () => {
    controls([trigger()])
    fireEvent.click(screen.getByTestId('trigger-fire'))
    fireEvent.click(screen.getByTestId('trigger-stop'))
    expect(screen.getByTestId('trigger-stop')).toHaveTextContent('Stop — click again')
    expect(screen.getByTestId('trigger-fire')).not.toHaveTextContent('click again')
  })

  it('offers Resume on a paused one, Stop only on a triggered one, a pointer on a pending one', () => {
    controls([
      trigger({ id: 'p', status: 'paused', statusReason: 'paused: nothing to sell' }),
      trigger({ id: 't', status: 'triggered' }),
      PENDING,
      trigger({
        id: 'd',
        status: 'done',
        statusReason: 'sold 0.05 ETH for 189.4 USDC at $3,788',
        updatedAt: '2026-10-04T05:50:00Z',
      }),
      { ...ALERT, id: 'a', status: 'done', statusReason: 'alerted at $5,012' },
    ])
    const rows = screen.getAllByTestId('trigger-row') as HTMLElement[]
    const byId = (id: string) => rows.find((r) => r.getAttribute('data-trigger') === id)!
    const paused = byId('p')
    expect(within(paused).getByTestId('trigger-word')).toHaveTextContent('Paused')
    expect(within(paused).getByTestId('trigger-resume')).toBeInTheDocument()
    expect(within(paused).getByTestId('trigger-fire')).toBeInTheDocument()
    expect(paused).toHaveAttribute('title', 'paused: nothing to sell')
    const fired = byId('t')
    expect(within(fired).getByTestId('trigger-word')).toHaveTextContent('Triggered · order open')
    expect(within(fired).queryByTestId('trigger-pause')).toBeNull()
    expect(within(fired).queryByTestId('trigger-fire')).toBeNull()
    expect(within(fired).getByTestId('trigger-stop')).toBeInTheDocument()
    const pending = byId('trg_99')
    expect(within(pending).getByTestId('trigger-word')).toHaveTextContent('Awaiting approval')
    expect(pending).toHaveTextContent('Review it above the composer')
    expect(within(pending).queryByRole('button')).toBeNull()
    const done = byId('d')
    expect(within(done).getByTestId('trigger-word')).toHaveTextContent('Done')
    expect(done).toHaveAttribute('title', 'sold 0.05 ETH for 189.4 USDC at $3,788')
    expect(within(done).queryByRole('button')).toBeNull()
    // An alert's Fire now keeps its name.
    expect(byId('a')).toHaveAttribute('data-action', 'alert')
  })

  it('says a bare user pause as “Paused by you”, and marks a trigger near its line', () => {
    controls([trigger({ id: 'p', status: 'paused', statusReason: 'user' }), near({ id: 'n' })])
    const rows = screen.getAllByTestId('trigger-row') as HTMLElement[]
    expect(rows[0]).toHaveAttribute('title', 'Paused by you')
    expect(rows[1]).toHaveAttribute('data-near', 'true')
  })

  it('locks the row of a trigger with a write in flight', () => {
    controls([trigger()], { triggerBusy: 'trg_1a2b3c4d' })
    expect(screen.getByTestId('trigger-pause')).toBeDisabled()
    expect(screen.getByTestId('trigger-fire')).toBeDisabled()
    expect(screen.getByTestId('trigger-stop')).toBeDisabled()
  })

  it('shows two finished rows at most, then "+N more"', () => {
    controls([
      trigger(),
      trigger({ id: 'b', status: 'stopped' }),
      trigger({ id: 'a', status: 'done' }),
      trigger({ id: 'c', status: 'expired' }),
    ])
    expect(screen.getAllByTestId('trigger-row')).toHaveLength(3)
    fireEvent.click(screen.getByTestId('trigger-more'))
    expect(screen.getAllByTestId('trigger-row')).toHaveLength(4)
  })

  it('names triggers in the strip above the composer; the status strip sums them in one chip', () => {
    renderDesk(
      <>
        <MissionStrip missions={[]} running={new Set()} pendingApprovals={0} triggers={[near()]} />
        <StatusStrip mode="trading" onSwitchMode={vi.fn()} triggers={[near(), BUY]} />
      </>,
    )
    expect(screen.getByTestId('mission-strip-trigger')).toHaveTextContent(
      'Stop-loss ETHArmed · ETH $3,811 · −0.3 %',
    )
    expect(screen.getByTestId('mission-strip-trigger')).toHaveAttribute('data-near', 'true')
    const chip = screen.getByTestId('strip-triggers')
    expect(chip).toHaveTextContent(/^Triggers ×2 · near$/)
    expect(chip).toHaveAttribute('data-state', 'near')
  })

  it('gives each chip one of the two mission slots', () => {
    const jobs = [
      { id: 'j1', name: 'Watch ETH', enabled: true },
      { id: 'j2', name: 'Watch BTC', enabled: true },
    ]
    renderDesk(
      <StatusStrip
        mode="trading"
        onSwitchMode={vi.fn()}
        missions={jobs}
        mandates={[MANDATE]}
        triggers={[trigger({ status: 'triggered' })]}
      />,
    )
    expect(screen.getByTestId('strip-triggers')).toHaveTextContent(/^Trigger · fired$/)
    expect(screen.getByTestId('strip-mandates')).toBeInTheDocument()
    const strip = screen.getByTestId('status-strip')
    expect(strip).not.toHaveTextContent('Watch ETH')
    expect(strip).toHaveTextContent('+2')
  })
})

describe('useMissions · triggers', () => {
  beforeEach(() => {
    useConnection.getState().setState('connected')
    rpcCall.mockReset()
    for (const fn of Object.values(toasts)) fn.mockReset()
  })

  function wrapper({ children }: { children: ReactNode }) {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
    })
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>
  }

  const other = trigger({ id: 'trg_o', sessionKey: 'agent:trading:webchat:other' })
  const unfiled = trigger({ id: 'trg_u', sessionKey: null })
  const pendingElsewhere = { ...PENDING, sessionKey: 'agent:main:webchat:x' }

  function answer(method: string, params: Record<string, unknown>) {
    if (method === 'cron.list') return []
    if (method === 'trading.dca.list') return { kind: 'mandates', mandates: [] }
    if (method === 'trading.trigger.list') {
      return {
        version: 1,
        kind: 'triggers',
        fetchedAt: PAYLOAD.fetchedAt,
        warnings: [],
        triggers: params.all ? [trigger(), other, unfiled, pendingElsewhere] : [],
        totals: { count: 4, armed: 3, awaiting: 1, triggered: 0 },
      }
    }
    if (method.startsWith('trading.trigger.')) return PAYLOAD
    return {}
  }

  it('lists this desk’s triggers and every pending one, and leaves them armed on Start fresh', async () => {
    rpcCall.mockImplementation(async (method: string, params: Record<string, unknown>) =>
      answer(method, params),
    )
    const { result } = renderHook(() => useMissions(SESSION), { wrapper })
    await waitFor(() => expect(result.current.triggers.length).toBeGreaterThan(0))
    expect(rpcCall).toHaveBeenCalledWith('trading.trigger.list', { all: true })
    expect(result.current.triggers.map((tr) => tr.id)).toEqual(['trg_1a2b3c4d', 'trg_u'])
    expect(result.current.awaitingTriggers.map((tr) => tr.id)).toEqual(['trg_99'])
    await act(async () => {
      await result.current.pauseAll()
    })
    expect(rpcCall).not.toHaveBeenCalledWith('trading.trigger.pause', expect.anything())
  })

  it('steers and toasts a trigger through trading.trigger.*', async () => {
    rpcCall.mockImplementation(async (method: string, params: Record<string, unknown>) =>
      answer(method, params),
    )
    const { result } = renderHook(() => useMissions(SESSION), { wrapper })
    await waitFor(() => expect(result.current.triggers.length).toBeGreaterThan(0))
    const tr = result.current.triggers[0]!
    const id = { id: 'trigger-trg_1a2b3c4d' }
    for (const [action, method, said] of [
      ['approve', 'trading.trigger.approve', 'Trigger approved and armed · Stop-loss ETH'],
      ['reject', 'trading.trigger.reject', 'Trigger rejected · Stop-loss ETH'],
      ['pause', 'trading.trigger.pause', 'Trigger paused · Stop-loss ETH'],
      ['resume', 'trading.trigger.resume', 'Trigger armed again · Stop-loss ETH'],
      ['stop', 'trading.trigger.stop', 'Trigger stopped · Stop-loss ETH'],
    ] as const) {
      await act(async () => {
        await result.current.trigger[action](tr)
      })
      expect(rpcCall).toHaveBeenCalledWith(method, { triggerId: 'trg_1a2b3c4d' })
      expect(toasts.success).toHaveBeenLastCalledWith(said, id)
    }
    await act(async () => {
      await result.current.trigger.stop(tr, 'changed my mind')
    })
    expect(rpcCall).toHaveBeenCalledWith('trading.trigger.stop', {
      triggerId: 'trg_1a2b3c4d',
      reason: 'changed my mind',
    })
  })

  it('says why when the engine refuses, and resolves null', async () => {
    rpcCall.mockImplementation(async (method: string, params: Record<string, unknown>) => {
      if (method === 'trading.trigger.pause')
        throw new Error('trading.trigger.bad_state: already stopped')
      return answer(method, params)
    })
    const { result } = renderHook(() => useMissions(SESSION), { wrapper })
    await waitFor(() => expect(result.current.triggers.length).toBeGreaterThan(0))
    let res: unknown = 'unset'
    await act(async () => {
      res = await result.current.trigger.pause(result.current.triggers[0]!)
    })
    expect(res).toBeNull()
    expect(toasts.error).toHaveBeenCalledWith(
      'Could not update the trigger: trading.trigger.bad_state: already stopped',
      { id: 'trigger-trg_1a2b3c4d' },
    )
  })

  it('says what a Fire now did: alerted, placed, filled, parked, skipped and why, failed and why', async () => {
    const fire = (extra: Partial<TriggerFire>): TriggerFire => ({
      n: 2,
      at: '2026-10-04T06:01:00Z',
      manual: true,
      status: 'pending',
      reasonCode: null,
      reason: null,
      priceUsd: 3790,
      orderId: 'ord_9',
      txHash: null,
      explorerUrl: null,
      ...extra,
    })
    let answerFire: TriggerFire | null = null
    rpcCall.mockImplementation(async (method: string, params: Record<string, unknown>) =>
      method === 'trading.trigger.fire'
        ? { ...PAYLOAD, ...(answerFire ? { fire: answerFire } : {}) }
        : answer(method, params),
    )
    const { result } = renderHook(() => useMissions(SESSION), { wrapper })
    await waitFor(() => expect(result.current.triggers.length).toBeGreaterThan(0))
    const tr = result.current.triggers[0]!
    const id = { id: 'trigger-trg_1a2b3c4d' }
    const fireNow = async (f: TriggerFire | null) => {
      answerFire = f
      await act(async () => {
        await result.current.trigger.fire(tr)
      })
    }
    await fireNow(fire({ status: 'skipped', reason: 'paused: nothing to sell' }))
    expect(toasts.warning).toHaveBeenLastCalledWith(
      'Fire skipped: paused: nothing to sell · Stop-loss ETH',
      id,
    )
    expect(toasts.success).not.toHaveBeenCalled()
    await fireNow(fire({ status: 'failed', reason: 'no route' }))
    expect(toasts.error).toHaveBeenLastCalledWith('Fire failed: no route · Stop-loss ETH', id)
    await fireNow(fire({ status: 'pending' }))
    expect(toasts.success).toHaveBeenLastCalledWith('Fired; order placed · Stop-loss ETH', id)
    await fireNow(fire({ status: 'filled' }))
    expect(toasts.success).toHaveBeenLastCalledWith('Fired and filled · Stop-loss ETH', id)
    await fireNow(fire({ status: 'alerted', orderId: null }))
    expect(toasts.success).toHaveBeenLastCalledWith('Alert sent · Stop-loss ETH', id)
    await fireNow(fire({ status: 'parked' }))
    expect(toasts.info).toHaveBeenLastCalledWith(
      'Fired; the order waits for your approval · Stop-loss ETH',
      id,
    )
    expect(rpcCall).toHaveBeenCalledWith('trading.trigger.fire', { triggerId: 'trg_1a2b3c4d' })
    // No `fire` in the answer and no manual fire in the history: said, not silent.
    await fireNow(null)
    expect(toasts.info).toHaveBeenLastCalledWith('Nothing fired · Stop-loss ETH', id)
  })
})
