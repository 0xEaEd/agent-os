import { fireEvent, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useGateway } from '~/stores/gateway'
import { renderDesk } from './test-utils'
import { TradingView } from './TradingView'

const rpcCall = vi.fn()
vi.mock('@/app/providers', () => ({
  useRpc: () => ({ call: rpcCall, waitForConnection: async () => {}, on: () => () => {} }),
}))
vi.mock('~/lib/desktop-api', () => ({
  desktopApi: () => ({ app: { openExternal: vi.fn(async () => {}) } }),
  isDesktop: () => true,
}))

beforeEach(() => {
  rpcCall.mockReset()
  useGateway.setState({ status: { state: 'running', pid: 1, url: 'http://x', error: null } })
})

describe('TradingView · gate', () => {
  it('says it is loading while the first status is still on its way', () => {
    rpcCall.mockImplementation(() => new Promise(() => {}))
    renderDesk(<TradingView />)
    expect(screen.getByTestId('trading-loading')).toHaveTextContent('Loading the desk')
  })

  it('reports a status error as the gateway not answering, with a retry', async () => {
    let fail = true
    rpcCall.mockImplementation(async (method: string) => {
      if (method === 'trading.status') {
        if (fail) throw new Error('socket closed')
        return {
          enabled: false,
          apiKeyConfigured: false,
          chains: [],
          limits: { approvalThresholdUsd: 100, dailyCapUsd: 1000, approvalTtlSeconds: 900 },
          unlockMode: 'auto',
          unlocked: false,
          syncing: false,
          lastSyncAt: null,
        }
      }
      if (method === 'wallet.status') throw new Error('socket closed')
      return {}
    })
    renderDesk(<TradingView />)
    const retry = await screen.findByTestId('trading-retry')
    expect(screen.getByRole('heading')).toHaveTextContent('Waiting for the gateway')
    // Never "Create your vault": nobody knows whether there is one.
    expect(screen.queryByTestId('vault-setup')).toBeNull()
    const before = rpcCall.mock.calls.filter((c) => c[0] === 'trading.status').length
    fail = false
    fireEvent.click(retry)
    await waitFor(() =>
      expect(rpcCall.mock.calls.filter((c) => c[0] === 'trading.status').length).toBeGreaterThan(
        before,
      ),
    )
  })
})
