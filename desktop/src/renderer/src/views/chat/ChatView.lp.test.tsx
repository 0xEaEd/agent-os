import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render } from '@testing-library/react'
import { createRef, type ReactNode } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { LpActions } from '@/views/chat/transcript/lp'
import { useConnection } from '@/stores/connection'
import { useGateway } from '~/stores/gateway'
import type { DeskProps } from '~/views/trading/desk/useDeskInstruments'
import { ChatView } from './ChatView'

// What the chat hands the shared transcript and the desk, captured per render:
// the LP write buttons exist only at the desk, and an order placed from one
// joins the desk's asks.
const KEY = 'agent:trading:webchat:lp-test'
const rpc = {
  waitForConnection: vi.fn(async () => {}),
  call: vi.fn(async () => ({})),
  on: vi.fn(() => () => {}),
}
vi.mock('@/app/providers', () => ({ useRpc: () => rpc }))
vi.mock('@/views/chat/RoutePicker', () => ({ RoutePicker: () => null }))

const seen: {
  lpActions: LpActions | null | undefined
  dcaActions: LpActions | null | undefined
  own: ReadonlySet<string>
  focus: unknown
} = { lpActions: undefined, dcaActions: undefined, own: new Set(), focus: null }
// What the desk instruments render into the chat's slots (null = nothing).
const slots: { region: ReactNode; seats: ReactNode } = { region: null, seats: null }

vi.mock('@/views/chat/useTranscript', () => ({
  useTranscript: (opts: { lpActions?: LpActions | null; dcaActions?: LpActions | null }) => {
    seen.lpActions = opts.lpActions
    seen.dcaActions = opts.dcaActions
    return {
      containerRef: createRef(),
      routerFxDockRef: createRef(),
      send: vi.fn(),
      abort: vi.fn(),
      busy: false,
      routerFxEnabled: false,
      setRouterFxEnabled: vi.fn(),
      history: [],
      runState: { status: 'idle', label: '' },
      pinnedToTail: true,
      scrollToTail: vi.fn(),
      isCompactInFlightForCurrentSession: () => false,
      setStreamIdlePausedForApproval: vi.fn(),
      setPendingDelegates: vi.fn(),
    }
  },
}))

vi.mock('~/views/trading/desk/useDeskInstruments', () => ({
  useDeskInstruments: (
    _desk: unknown,
    ctx: { ownOrderIds?: ReadonlySet<string>; focusOrderId: string | null },
  ) => {
    seen.own = ctx.ownOrderIds ?? new Set()
    seen.focus = ctx.focusOrderId
    return {
      region: slots.region,
      dockAbove: null,
      seats: slots.seats,
      placeholder: undefined,
      still: false,
      modal: null,
      emptyHint: null,
      onFocusChange: () => {},
    }
  },
}))

function mount(desk: DeskProps | null) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MemoryRouter initialEntries={[`/sessions/${encodeURIComponent(KEY)}`]}>
      <QueryClientProvider client={client}>
        <Routes>
          <Route path="/sessions/:key?" element={<ChatView desk={desk} />} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  slots.region = null
  slots.seats = null
  seen.lpActions = undefined
  seen.dcaActions = undefined
  seen.own = new Set()
  seen.focus = null
  rpc.call.mockClear()
  useConnection.getState().setState('connected')
  useGateway.setState({ status: { state: 'running', pid: 1, url: 'http://x', error: null } })
})

describe('ChatView · LP write actions', () => {
  it('hands the transcript no write actions in a plain chat', () => {
    mount(null)
    expect(seen.lpActions).toBeNull()
  })

  it('at the desk: calls over the operator connection and adopts the parked order', async () => {
    const desk = { entering: false, onFirstSend: vi.fn() } as unknown as DeskProps
    mount(desk)
    const actions = seen.lpActions
    expect(actions).toBeTruthy()
    await actions!.call('trading.lp.collect', { tokenId: '48213', chainId: 8453 })
    expect(rpc.call).toHaveBeenCalledWith('trading.lp.collect', { tokenId: '48213', chainId: 8453 })
    act(() => actions!.onOrder?.('lpo_c41a9e'))
    expect([...seen.own]).toEqual(['lpo_c41a9e'])
    expect(seen.focus).toBe('lpo_c41a9e')
  })
})

describe('ChatView · DCA card actions', () => {
  // The DCA card's buttons (Approve & start, Pause, Buy now, Stop) exist only
  // where the chat hands the renderer `dcaActions`: the desk, never a plain chat.
  it('hands the transcript no DCA actions in a plain chat', () => {
    mount(null)
    expect(seen.dcaActions).toBeNull()
  })

  it('at the desk: calls trading.dca.* over the operator connection and adopts a parked buy', async () => {
    const desk = { entering: false, onFirstSend: vi.fn() } as unknown as DeskProps
    mount(desk)
    const actions = seen.dcaActions
    expect(actions).toBeTruthy()
    await actions!.call('trading.dca.approve', { mandateId: 'dca_1a2b3c4d' })
    expect(rpc.call).toHaveBeenCalledWith('trading.dca.approve', { mandateId: 'dca_1a2b3c4d' })
    act(() => actions!.onOrder?.('ord_7c2e91'))
    expect([...seen.own]).toEqual(['ord_7c2e91'])
    expect(seen.focus).toBe('ord_7c2e91')
  })
})

describe('ChatView · the approvals region and the composer chips', () => {
  // A card that overflowed the region used to put Approve/Reject exactly
  // where the quick-action chips are (elementFromPoint on Reject hit "Send").
  // The region is its own flex row of the stage, after the transcript and
  // before the composer block — never inside it, never after the chips.
  it('docks the region between the transcript and the composer, outside the composer shell', () => {
    slots.region = <div className="trd-asks" data-testid="approvals-region" />
    slots.seats = <div className="trd-seats" data-testid="composer-seats" />
    const desk = { entering: false, onFirstSend: vi.fn() } as unknown as DeskProps
    const { container } = mount(desk)
    const stage = container.querySelector('.chat-stage')!
    const region = container.querySelector('[data-testid=approvals-region]')!
    const seats = container.querySelector('[data-testid=composer-seats]')!
    const thread = container.querySelector('.chat-thread')!
    const shell = container.querySelector('.composer-shell')!
    expect(region.parentElement).toBe(stage)
    expect(shell.contains(seats)).toBe(true)
    expect(shell.contains(region)).toBe(false)
    const follows = (a: Element, b: Element) =>
      Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)
    expect(follows(thread, region)).toBe(true)
    expect(follows(region, seats)).toBe(true)
    expect(follows(region, shell)).toBe(true)
  })
})
