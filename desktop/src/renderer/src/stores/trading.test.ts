import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useConnection } from '@/stores/connection'
import { TRADING_KEYS, useOrders } from './trading'

const rpcCall = vi.fn()
vi.mock('@/app/providers', () => ({
  useRpc: () => ({ call: rpcCall, waitForConnection: async () => {}, on: () => () => {} }),
}))

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return createElement(QueryClientProvider, { client }, children)
}

beforeEach(() => {
  useConnection.getState().setState('connected')
  rpcCall.mockReset()
  rpcCall.mockResolvedValue({ orders: [] })
})

describe('TRADING_KEYS.orders', () => {
  it('keys the page size and the wallet, so two pages never share one entry', () => {
    expect(TRADING_KEYS.orders(undefined, 50)).not.toEqual(TRADING_KEYS.orders(undefined, 100))
    expect(TRADING_KEYS.orders('awaiting_approval', 20)).not.toEqual(
      TRADING_KEYS.orders(undefined, 20),
    )
    expect(TRADING_KEYS.orders(undefined, 50, '0xabc')).not.toEqual(
      TRADING_KEYS.orders(undefined, 50),
    )
    expect(TRADING_KEYS.orders(undefined, 50)).toEqual(TRADING_KEYS.orders(undefined, 50))
    // Every orders key still lives under the one prefix the invalidator sweeps.
    expect(TRADING_KEYS.orders(undefined, 50).slice(0, 2)).toEqual(['trading', 'orders'])
  })
})

describe('useOrders', () => {
  it('fetches each page size on its own', async () => {
    renderHook(
      () => {
        useOrders(undefined, true, 50)
        useOrders(undefined, true, 100)
      },
      { wrapper },
    )
    await waitFor(() =>
      expect(rpcCall.mock.calls.filter((c) => c[0] === 'trading.orders.list')).toHaveLength(2),
    )
    const limits = rpcCall.mock.calls
      .filter((c) => c[0] === 'trading.orders.list')
      .map((c) => (c[1] as { limit: number }).limit)
      .sort((a, b) => a - b)
    expect(limits).toEqual([50, 100])
  })
})
