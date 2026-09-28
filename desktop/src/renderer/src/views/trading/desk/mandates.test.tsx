import { readFileSync } from 'node:fs'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, renderHook, screen, waitFor, within } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useConnection } from '@/stores/connection'
import { order, renderDesk, WALLET } from '../test-utils'
import type { Mandate, MandatePayload } from '../types'
import { ApprovalsRegion } from './ApprovalsRegion'
import { MandateCard } from './MandateCard'
import {
  countdown,
  dcaFormFromMandate,
  dcaUpdatePatch,
  deskMandates,
  everyShort,
  mandateState,
  parseEvery,
} from './mandate-logic'
import { MissionControls, MissionStrip } from './MissionControls'
import { useMissions } from './missions'
import { StatusStrip } from './StatusStrip'

const rpcCall = vi.fn()
vi.mock('@/app/providers', () => ({
  useRpc: () => ({ call: rpcCall, waitForConnection: async () => {}, on: () => () => {} }),
}))
const toasts = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn() }))
vi.mock('sonner', () => ({ toast: toasts }))

const PAYLOAD = JSON.parse(
  readFileSync('src/renderer/src/views/trading/desk/__fixtures__/dca/mandate.json', 'utf8'),
) as MandatePayload
const SESSION = 'agent:trading:webchat:desk-test'
// The fixture's next buy is 2026-09-28T09:00Z; the clock sits 3 h 12 m before it.
const NOW = Date.parse('2026-09-28T05:48:00Z')

function mandate(extra: Partial<Mandate> = {}): Mandate {
  return { ...PAYLOAD.mandate, ...extra }
}

const PENDING = mandate({
  id: 'dca_99',
  name: 'DCA cbBTC',
  status: 'awaiting_approval',
  initiator: 'agent',
  approvedAt: null,
  expiresAt: '2026-09-29T06:00:00Z',
  budget: { ...PAYLOAD.mandate.budget, usdPerRun: 150, spentUsd: 0, reservedUsd: 0, progress: 0 },
  guards: { ...PAYLOAD.mandate.guards, buysNeedApproval: true },
  runs: { done: 0, max: null, skipped: 0, failed: 0, attempts: 0 },
  history: [],
})

describe('mandate logic', () => {
  it('reads the engine figures into words', () => {
    expect(countdown(3 * 3_600_000 + 12 * 60_000)).toBe('3 h 12 m')
    expect(countdown(12 * 60_000)).toBe('12 m')
    expect(countdown(30_000)).toBe('under a minute')
    expect(countdown(2 * 86_400_000 + 4 * 3_600_000)).toBe('2 d 4 h')
    expect(parseEvery('30m')).toBe(1_800)
    expect(parseEvery('1d')).toBe(86_400)
    expect(parseEvery('3600')).toBe(3_600)
    expect(parseEvery('soon')).toBeNull()
    expect(everyShort(86_400)).toBe('day')
    expect(everyShort(21_600)).toBe('6 h')
    expect(mandateState(mandate(), NOW)).toEqual({
      key: 'trading.dca.state.active',
      next: '3 h 12 m',
      due: false,
    })
    expect(mandateState(mandate(), Date.parse('2026-09-28T09:00:05Z')).due).toBe(true)
    expect(mandateState(PENDING, NOW).key).toBe('trading.dca.state.awaiting')
  })

  it('lists this chat’s mandates and unfiled ones; a finished one for a day only', () => {
    const other = mandate({ id: 'dca_o', sessionKey: 'agent:trading:webchat:other' })
    const unfiled = mandate({ id: 'dca_u', sessionKey: null })
    const doneToday = mandate({
      id: 'dca_d',
      status: 'completed',
      updatedAt: '2026-09-28T01:00:00Z',
    })
    const doneLastWeek = mandate({
      id: 'dca_w',
      status: 'completed',
      updatedAt: '2026-09-20T01:00:00Z',
    })
    const stopped = mandate({ id: 'dca_s', status: 'stopped' })
    const ids = deskMandates(
      [mandate(), other, unfiled, doneToday, doneLastWeek, stopped],
      SESSION,
      NOW,
    ).map((m) => m.id)
    expect(ids).toEqual(['dca_1a2b3c4d', 'dca_u', 'dca_d'])
  })

  it('clears a limit on update with a 0, as the engine reads it', () => {
    const m = mandate()
    const form = { ...dcaFormFromMandate(m), runs: '', maxPrice: '' }
    expect(dcaUpdatePatch(form, m)).toEqual({ runsMax: 0, maxPriceUsd: 0 })
    expect(dcaUpdatePatch(dcaFormFromMandate(m), m)).toEqual({})
  })
})

describe('MandateCard', () => {
  it('says the whole mandate before the one button that starts it', () => {
    const onApprove = vi.fn()
    const onReject = vi.fn()
    renderDesk(
      <MandateCard
        mandate={PENDING}
        wallets={[WALLET]}
        deciding={false}
        onApprove={onApprove}
        onReject={onReject}
      />,
    )
    const card = screen.getByTestId('mandate-card')
    expect(card).toHaveAttribute('data-status', 'awaiting_approval')
    expect(screen.getByTestId('mandate-legs')).toHaveTextContent('ETH←USDC$150 every day')
    const fact = (key: string) => card.querySelector(`[data-fact='${key}'] dd`)?.textContent
    expect(fact('buy')).toBe('$150 of USDC')
    expect(fact('every')).toBe('every day')
    expect(fact('cap')).toBe('$300')
    expect(fact('buys')).toBe('until the cap')
    expect(fact('maxPrice')).toBe('$3,000')
    expect(fact('wallet')).toBe('Main · 0x1111…1111')
    expect(fact('chain')).toBe('Base')
    expect(fact('first')).toBe('on approval')
    expect(fact('expires')).toBeTruthy()
    expect(screen.getByTestId('mandate-needs-approval')).toBeInTheDocument()
    expect(screen.getByTestId('mandate-warnings')).toHaveTextContent(
      'Each buy of $150 is above the $100 approval threshold and will wait for you.',
    )
    fireEvent.click(screen.getByTestId('mandate-approve'))
    expect(onApprove).toHaveBeenCalledWith(PENDING)
    expect(screen.getByTestId('mandate-approve')).toHaveTextContent('Approve & start')
    fireEvent.click(screen.getByTestId('mandate-reject'))
    expect(onReject).toHaveBeenCalledWith(PENDING)
  })

  it('locks both buttons while the decision is in flight', () => {
    renderDesk(
      <MandateCard
        mandate={PENDING}
        wallets={[WALLET]}
        deciding
        onApprove={vi.fn()}
        onReject={vi.fn()}
      />,
    )
    expect(screen.getByTestId('mandate-approve')).toBeDisabled()
    expect(screen.getByTestId('mandate-reject')).toBeDisabled()
  })

  it('docks above the pending orders in the approvals region', () => {
    const onApproveMandate = vi.fn()
    renderDesk(
      <ApprovalsRegion
        pending={[order({ orderId: 'o1', status: 'awaiting_approval', initiator: 'agent' })]}
        settled={[]}
        wallets={[WALLET]}
        deciding={null}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        focusOrderId={null}
        mandates={[PENDING]}
        onApproveMandate={onApproveMandate}
        onRejectMandate={vi.fn()}
      />,
    )
    const region = screen.getByTestId('approvals-region')
    const cards = [...region.querySelectorAll('article')]
    expect(cards[0]).toHaveAttribute('data-testid', 'mandate-card')
    expect(cards[1]).toHaveAttribute('data-testid', 'approval-card')
    fireEvent.click(within(cards[0] as HTMLElement).getByTestId('mandate-approve'))
    expect(onApproveMandate).toHaveBeenCalledWith(PENDING)
  })

  it('renders the region for a pending mandate alone', () => {
    renderDesk(
      <ApprovalsRegion
        pending={[]}
        settled={[]}
        wallets={[WALLET]}
        deciding={null}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        focusOrderId={null}
        mandates={[PENDING]}
      />,
    )
    expect(screen.getByTestId('mandate-card')).toBeInTheDocument()
  })
})

describe('mandates among the missions', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(NOW)
  })
  afterEach(() => vi.useRealTimers())

  function controls(mandates: Mandate[], extra: Record<string, unknown> = {}) {
    const handlers = {
      onMandatePause: vi.fn(),
      onMandateResume: vi.fn(),
      onMandateRun: vi.fn(),
      onMandateEdit: vi.fn(),
      onMandateStop: vi.fn(),
    }
    renderDesk(
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
        mandates={mandates}
        {...handlers}
        {...extra}
      />,
    )
    return handlers
  }

  it('shows an active mandate with its countdown, spent of cap, and Pause · Buy now · Edit · Stop', () => {
    const h = controls([mandate()])
    const row = screen.getByTestId('mandate-row')
    expect(row).toHaveAttribute('data-state', 'active')
    expect(row).toHaveTextContent('DCA ETH')
    expect(screen.getByTestId('mandate-word')).toHaveTextContent('Active · next 3 h 12 m')
    expect(screen.getByTestId('mandate-progress')).toHaveTextContent('$120 / $300')
    expect(screen.getByTestId('mandate-progress')).toHaveAttribute('aria-valuenow', '40')
    expect(screen.queryByTestId('mandate-resume')).toBeNull()
    fireEvent.click(screen.getByTestId('mandate-pause'))
    expect(h.onMandatePause).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByTestId('mandate-run'))
    expect(h.onMandateRun).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByTestId('mandate-edit'))
    expect(h.onMandateEdit).toHaveBeenCalledWith(expect.objectContaining({ id: 'dca_1a2b3c4d' }))
  })

  it('asks for a second click before a Stop, inline and never through a dialog', () => {
    const confirm = vi.spyOn(window, 'confirm')
    const h = controls([mandate()])
    fireEvent.click(screen.getByTestId('mandate-stop'))
    expect(h.onMandateStop).not.toHaveBeenCalled()
    expect(screen.getByTestId('mandate-stop')).toHaveTextContent('Stop — click again')
    fireEvent.click(screen.getByTestId('mandate-stop'))
    expect(h.onMandateStop).toHaveBeenCalledTimes(1)
    expect(confirm).not.toHaveBeenCalled()
  })

  it('offers Resume on a paused mandate, only a pointer on a pending one, nothing on a done one', () => {
    controls([
      mandate({ id: 'p', status: 'paused', statusReason: 'user' }),
      PENDING,
      mandate({ id: 'd', status: 'completed', statusReason: 'cap reached' }),
    ])
    const [paused, pending, done] = screen.getAllByTestId('mandate-row') as HTMLElement[]
    expect(within(paused!).getByTestId('mandate-word')).toHaveTextContent('Paused')
    expect(within(paused!).getByTestId('mandate-resume')).toBeInTheDocument()
    expect(within(paused!).getByTestId('mandate-run')).toBeInTheDocument()
    expect(within(pending!).getByTestId('mandate-word')).toHaveTextContent('Awaiting approval')
    expect(within(pending!).queryByTestId('mandate-pause')).toBeNull()
    expect(within(pending!).queryByTestId('mandate-stop')).toBeNull()
    expect(within(done!).getByTestId('mandate-word')).toHaveTextContent('Done')
    expect(within(done!).queryByRole('button')).toBeNull()
  })

  it('locks the row of a mandate with a write in flight', () => {
    controls([mandate()], { mandateBusy: 'dca_1a2b3c4d' })
    expect(screen.getByTestId('mandate-pause')).toBeDisabled()
    expect(screen.getByTestId('mandate-run')).toBeDisabled()
  })

  it('names mandates in the strip above the composer and in the status strip', () => {
    renderDesk(
      <>
        <MissionStrip
          missions={[]}
          running={new Set()}
          pendingApprovals={0}
          mandates={[mandate()]}
        />
        <StatusStrip mode="trading" onSwitchMode={vi.fn()} mandates={[mandate(), PENDING]} />
      </>,
    )
    expect(screen.getByTestId('mission-strip-mandate')).toHaveTextContent(
      'DCA ETHActive · next 3 h 12 m$120 / $300',
    )
    const strip = screen.getAllByTestId('strip-mandate')
    expect(strip).toHaveLength(2)
    expect(strip[1]).toHaveTextContent('Awaiting approval')
  })
})

describe('useMissions · mandates', () => {
  beforeEach(() => {
    useConnection.getState().setState('connected')
    rpcCall.mockReset()
    toasts.success.mockReset()
    toasts.error.mockReset()
  })

  function wrapper({ children }: { children: ReactNode }) {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
    })
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>
  }

  const other = mandate({ id: 'dca_o', sessionKey: 'agent:trading:webchat:other' })
  const unfiled = mandate({ id: 'dca_u', sessionKey: null })
  const pendingElsewhere = { ...PENDING, sessionKey: 'agent:main:webchat:x' }

  function answer(method: string, params: Record<string, unknown>) {
    if (method === 'cron.list') return []
    if (method === 'trading.dca.list') {
      return {
        version: 1,
        kind: 'mandates',
        fetchedAt: PAYLOAD.fetchedAt,
        warnings: [],
        mandates: params.all ? [mandate(), other, unfiled, pendingElsewhere] : [],
        totals: { count: 4, active: 3, spentUsd: 360, capUsd: 1200, acquiredUsd: null },
      }
    }
    if (method.startsWith('trading.dca.')) return PAYLOAD
    return {}
  }

  it('lists this desk’s mandates and every pending one, and pauses only its own on Start fresh', async () => {
    rpcCall.mockImplementation(async (method: string, params: Record<string, unknown>) =>
      answer(method, params),
    )
    const { result } = renderHook(() => useMissions(SESSION), { wrapper })
    await waitFor(() => expect(result.current.mandates.length).toBeGreaterThan(0))
    expect(rpcCall).toHaveBeenCalledWith('trading.dca.list', { all: true })
    expect(result.current.mandates.map((m) => m.id)).toEqual(['dca_1a2b3c4d', 'dca_u'])
    expect(result.current.awaitingMandates.map((m) => m.id)).toEqual(['dca_99'])
    let ok = false
    await act(async () => {
      ok = await result.current.pauseAll()
    })
    expect(ok).toBe(true)
    const pauses = rpcCall.mock.calls.filter(([m]) => m === 'trading.dca.pause').map(([, p]) => p)
    expect(pauses).toEqual([{ mandateId: 'dca_1a2b3c4d' }])
  })

  it('creates, steers and toasts a mandate through trading.dca.*', async () => {
    rpcCall.mockImplementation(async (method: string, params: Record<string, unknown>) =>
      answer(method, params),
    )
    const { result } = renderHook(() => useMissions(SESSION), { wrapper })
    await waitFor(() => expect(result.current.mandates.length).toBeGreaterThan(0))
    await act(async () => {
      await result.current.mandate.create({ chainId: 8453, token: 'ETH', usdPerRun: 10 })
    })
    expect(rpcCall).toHaveBeenCalledWith('trading.dca.create', {
      chainId: 8453,
      token: 'ETH',
      usdPerRun: 10,
    })
    expect(toasts.success).toHaveBeenLastCalledWith('DCA started · DCA ETH', { id: 'dca-new' })
    const m = result.current.mandates[0]!
    for (const [action, method] of [
      ['approve', 'trading.dca.approve'],
      ['pause', 'trading.dca.pause'],
      ['resume', 'trading.dca.resume'],
      ['run', 'trading.dca.run'],
      ['stop', 'trading.dca.stop'],
    ] as const) {
      await act(async () => {
        await result.current.mandate[action](m)
      })
      // The write, then the refetch its invalidation sets off.
      expect(rpcCall).toHaveBeenCalledWith(method, { mandateId: 'dca_1a2b3c4d' })
    }
    await act(async () => {
      await result.current.mandate.update(m, { capUsd: 500 })
    })
    expect(rpcCall).toHaveBeenCalledWith('trading.dca.update', {
      mandateId: 'dca_1a2b3c4d',
      capUsd: 500,
    })
  })

  it('says why when the engine refuses, and resolves null so the caller stays put', async () => {
    rpcCall.mockImplementation(async (method: string, params: Record<string, unknown>) => {
      if (method === 'trading.dca.stop') throw new Error('trading.dca.bad_state: already stopped')
      return answer(method, params)
    })
    const { result } = renderHook(() => useMissions(SESSION), { wrapper })
    await waitFor(() => expect(result.current.mandates.length).toBeGreaterThan(0))
    let res: unknown = 'unset'
    await act(async () => {
      res = await result.current.mandate.stop(result.current.mandates[0]!)
    })
    expect(res).toBeNull()
    expect(toasts.error).toHaveBeenCalledWith(
      'Could not update the DCA: trading.dca.bad_state: already stopped',
      { id: 'dca-dca_1a2b3c4d' },
    )
  })
})
