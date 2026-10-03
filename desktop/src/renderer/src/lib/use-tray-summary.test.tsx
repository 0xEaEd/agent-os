import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useApprovals } from '@/services/approval-monitor'
import { useConnection } from '@/stores/connection'
import type { TraySummary } from '@shared/tray'
import { useLive } from '~/stores/live'
import { mintTradingSessionKey } from '~/views/trading/desk/mode-logic'
import { writeTradingSessionKey } from '~/stores/trading-ui'
import { useTraySummary } from './use-tray-summary'

const rpcCall = vi.fn()
const handlers = new Map<string, Set<() => void>>()
vi.mock('@/app/providers', () => ({
  useRpc: () => ({
    call: rpcCall,
    waitForConnection: async () => {},
    on: (event: string, fn: () => void) => {
      const set = handlers.get(event) ?? new Set()
      set.add(fn)
      handlers.set(event, set)
      return () => set.delete(fn)
    },
  }),
}))

const setSummary = vi.fn<(s: TraySummary) => void>()

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return createElement(QueryClientProvider, { client }, children)
}

function emit(event: string): void {
  for (const fn of handlers.get(event) ?? []) fn()
}

function lastSummary(): TraySummary | undefined {
  return setSummary.mock.calls.at(-1)?.[0]
}

function trading(method: string): boolean {
  return rpcCall.mock.calls.some(([m]) => m === method)
}

const SOON = '2026-10-03T12:14:00.000Z'

beforeEach(() => {
  localStorage.clear()
  handlers.clear()
  rpcCall.mockReset()
  rpcCall.mockImplementation(async (method: string) => {
    if (method === 'trading.orders.list')
      return { orders: [{ orderId: 'o1', status: 'awaiting_approval' }], pendingApprovals: 1 }
    if (method === 'trading.dca.list')
      return {
        mandates: [
          {
            id: 'm1',
            name: 'DCA USDC',
            status: 'active',
            token: { symbol: 'USDC' },
            quote: { symbol: 'ETH' },
            schedule: { nextRunAt: SOON },
          },
        ],
      }
    return {}
  })
  setSummary.mockReset()
  ;(window as unknown as { agentos?: unknown }).agentos = {
    tray: { setSummary, onNavigate: () => () => {} },
  }
  useConnection.getState().setState('connected')
  useLive.setState({ ids: new Set() })
  useApprovals.setState({ pending: [] })
})

afterEach(() => {
  delete (window as unknown as { agentos?: unknown }).agentos
})

describe('useTraySummary', () => {
  it('reports live turns and tool approvals without waking the trading engine', async () => {
    useLive.setState({ ids: new Set(['s1', 's2']) })
    useApprovals.setState({ pending: [{ id: 'a1' }] })
    renderHook(() => useTraySummary(), { wrapper })
    await waitFor(() => expect(setSummary).toHaveBeenCalled())
    expect(lastSummary()).toEqual({
      liveTurns: 2,
      approvalsPending: 1,
      tradeApprovals: 0,
      nextMandate: null,
    })
    expect(trading('trading.orders.list')).toBe(false)
    expect(trading('trading.dca.list')).toBe(false)
  })

  it('adds desk orders and the next DCA buy once the desk has been used', async () => {
    writeTradingSessionKey(mintTradingSessionKey())
    renderHook(() => useTraySummary(), { wrapper })
    await waitFor(
      () =>
        expect(lastSummary()).toEqual({
          liveTurns: 0,
          approvalsPending: 1,
          tradeApprovals: 1,
          nextMandate: { label: 'ETH → USDC', at: SOON },
        }),
      { timeout: 3_000 },
    )
  })

  it('starts watching the desk when trading activity shows up in a chat', async () => {
    renderHook(() => useTraySummary(), { wrapper })
    await waitFor(() => expect(setSummary).toHaveBeenCalled())
    // A reconnect is not trading activity.
    act(() => emit('_hello'))
    await new Promise((r) => setTimeout(r, 200))
    expect(trading('trading.orders.list')).toBe(false)
    act(() => emit('trading.approval.requested'))
    await waitFor(() => expect(trading('trading.orders.list')).toBe(true))
    await waitFor(() => expect(lastSummary()?.tradeApprovals).toBe(1), { timeout: 3_000 })
  })

  it('pushes nothing in a browser tab', async () => {
    delete (window as unknown as { agentos?: unknown }).agentos
    renderHook(() => useTraySummary(), { wrapper })
    await new Promise((r) => setTimeout(r, 50))
    expect(setSummary).not.toHaveBeenCalled()
  })
})
