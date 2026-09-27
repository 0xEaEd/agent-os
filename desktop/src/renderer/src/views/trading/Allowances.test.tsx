import { fireEvent, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Allowances } from './Allowances'
import { NetworkPips, pipState, pipTitle } from './NetworkPips'
import { renderDesk, USDC, WALLET } from './test-utils'
import type { Allowance, NetworkChain } from './types'

const rpcCall = vi.fn()
vi.mock('@/app/providers', () => ({
  useRpc: () => ({ call: rpcCall, waitForConnection: async () => {}, on: () => () => {} }),
}))
const openExternal = vi.fn(async () => {})
vi.mock('~/lib/desktop-api', () => ({
  desktopApi: () => ({ app: { openExternal } }),
  isDesktop: () => true,
}))

const PERMIT2 = '0x000000000022D473030F116dDEE9F6B43aC78BA3'

function allowance(extra: Partial<Allowance> = {}): Allowance {
  return {
    chainId: 8453,
    wallet: WALLET.address,
    token: USDC,
    spender: PERMIT2,
    spenderLabel: 'Permit2',
    spenderUrl: 'https://basescan.org/address/' + PERMIT2,
    allowanceRaw: '115792089237316195423570985008687907853269984665640564039457584007913129639935',
    allowance: 'unlimited',
    unlimited: true,
    readFailed: false,
    balanceRaw: '900000000',
    balance: '900',
    exposureUsd: 900,
    lastBlock: 100,
    lastTxHash: '0xaa',
    explorerUrl: 'https://basescan.org/tx/0xaa',
    ...extra,
  }
}

beforeEach(() => {
  rpcCall.mockReset()
  openExternal.mockClear()
})

describe('Allowances', () => {
  it('lists live allowances, warns about unlimited ones, and revokes on the second click', async () => {
    rpcCall.mockImplementation(async (method: string) => {
      if (method === 'trading.allowances.list') {
        return {
          wallet: WALLET.address,
          chainId: null,
          count: 2,
          unlimitedCount: 1,
          allowances: [
            allowance(),
            allowance({
              spender: '0x2222222222222222222222222222222222222222',
              spenderLabel: null,
              spenderUrl: null,
              allowance: '5',
              unlimited: false,
              exposureUsd: 5,
              lastTxHash: null,
              explorerUrl: null,
            }),
          ],
        }
      }
      if (method === 'trading.allowances.revoke')
        return { order: { orderId: 'r1', kind: 'revoke', status: 'submitted' } }
      return {}
    })
    renderDesk(<Allowances wallet={WALLET.address} />)
    await waitFor(() => expect(screen.getAllByTestId('allowance-row')).toHaveLength(2))
    expect(screen.getByTestId('allowances-warn')).toHaveTextContent('1 unlimited allowance')
    const rows = screen.getAllByTestId('allowance-row')
    expect(rows[0]).toHaveAttribute('data-unlimited', 'true')
    expect(rows[0]).toHaveTextContent('Permit2')
    expect(rows[0]).toHaveTextContent('Unlimited')
    expect(rows[0]).toHaveTextContent('900 held')
    expect(rows[0]).toHaveTextContent('$900.00 at stake')
    expect(rows[1]).toHaveTextContent('Unknown contract')
    expect(rows[1]).toHaveTextContent('5 USDC')
    const revoke = screen.getAllByTestId('allowance-revoke')[0]!
    fireEvent.click(revoke)
    expect(revoke).toHaveTextContent('Click again to revoke')
    expect(rpcCall).not.toHaveBeenCalledWith('trading.allowances.revoke', expect.anything())
    fireEvent.click(revoke)
    await waitFor(() =>
      expect(rpcCall).toHaveBeenCalledWith('trading.allowances.revoke', {
        chainId: 8453,
        wallet: WALLET.address,
        token: USDC.address,
        spender: PERMIT2,
        initiator: 'manual',
      }),
    )
  })

  it('says the read failed, with a retry, rather than "nothing approved"', async () => {
    let fail = true
    rpcCall.mockImplementation(async () => {
      if (fail) throw new Error('socket closed')
      return { wallet: WALLET.address, chainId: null, count: 0, unlimitedCount: 0, allowances: [] }
    })
    renderDesk(<Allowances wallet={WALLET.address} />)
    await waitFor(() => expect(screen.getByText('socket closed')).toBeInTheDocument())
    expect(screen.queryByText('No live allowances')).toBeNull()
    fail = false
    fireEvent.click(screen.getByTestId('trading-error-retry'))
    await waitFor(() => expect(screen.getByText('No live allowances')).toBeInTheDocument())
  })

  it('leaves an unknown spender as plain text rather than a dead link', async () => {
    rpcCall.mockResolvedValue({
      wallet: WALLET.address,
      chainId: null,
      count: 1,
      unlimitedCount: 0,
      allowances: [allowance({ spenderLabel: null, spenderUrl: null, unlimited: false })],
    })
    renderDesk(<Allowances wallet={WALLET.address} />)
    await waitFor(() => expect(screen.getByTestId('allowance-row')).toBeInTheDocument())
    const addr = screen.getByTestId('allowance-row').querySelector('.trd-allow__addr')
    expect(addr?.tagName).toBe('SPAN')
  })

  it('says so when nothing is approved', async () => {
    rpcCall.mockResolvedValue({
      wallet: WALLET.address,
      chainId: null,
      count: 0,
      unlimitedCount: 0,
      allowances: [],
    })
    renderDesk(<Allowances wallet={WALLET.address} />)
    await waitFor(() => expect(screen.getByText('No live allowances')).toBeInTheDocument())
    expect(screen.queryByTestId('allowances-warn')).toBeNull()
  })
})

describe('NetworkPips', () => {
  const chain = (extra: Partial<NetworkChain> = {}): NetworkChain => ({
    chainId: 8453,
    key: 'base',
    name: 'Base',
    native: 'ETH',
    rpcUrl: 'https://lb.drpc.org/…',
    healthy: true,
    latencyMs: 80,
    blockNumber: 123,
    blockAgeS: 2,
    blockTimeS: 2,
    baseFeeGwei: 0.0123,
    priorityFeeGwei: 0.001,
    error: null,
    ...extra,
  })

  it('grades a chain by its head and errors', () => {
    expect(pipState(chain())).toBe('ok')
    expect(pipState(chain({ healthy: false }))).toBe('stale')
    expect(pipState(chain({ error: 'HTTP 502', blockNumber: null }))).toBe('down')
    const title = pipTitle(chain())
    expect(title).toContain('Base · Healthy')
    expect(title).toContain('Head #123 · Age 2s')
    expect(title).toContain('Base fee 0.0123 gwei · Tip 0.0010 gwei')
    expect(title).toContain('RPC 80 ms')
  })

  it('renders one pip per chain from the engine and refetches on click', async () => {
    rpcCall.mockResolvedValue({
      checkedAt: 1,
      chains: [
        chain(),
        chain({
          chainId: 4663,
          key: 'robinhood',
          name: 'Robinhood Chain',
          healthy: false,
          blockAgeS: 90,
        }),
      ],
    })
    renderDesk(<NetworkPips />)
    await waitFor(() => expect(screen.getByTestId('pip-8453')).toBeInTheDocument())
    expect(screen.getByTestId('pip-8453')).toHaveAttribute('data-state', 'ok')
    expect(screen.getByTestId('pip-4663')).toHaveAttribute('data-state', 'stale')
    const calls = rpcCall.mock.calls.length
    fireEvent.click(screen.getByTestId('pip-4663'))
    await waitFor(() => expect(rpcCall.mock.calls.length).toBeGreaterThan(calls))
  })
})
