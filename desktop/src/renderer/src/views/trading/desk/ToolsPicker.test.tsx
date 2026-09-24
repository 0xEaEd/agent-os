import { fireEvent, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderDesk, WALLET } from '../test-utils'
import { NetworkSheet } from './ToolSheets'
import { ToolsPicker } from './ToolsPicker'

const rpcCall = vi.fn()
vi.mock('@/app/providers', () => ({
  useRpc: () => ({ call: rpcCall, waitForConnection: async () => {}, on: () => () => {} }),
}))
vi.mock('~/lib/desktop-api', () => ({
  desktopApi: () => ({ app: { openExternal: vi.fn(async () => {}) } }),
  isDesktop: () => true,
}))

const chain = (extra: Record<string, unknown>) => ({
  chainId: 8453,
  key: 'base',
  name: 'Base',
  native: 'ETH',
  rpcUrl: 'https://lb.drpc.live/…',
  healthy: true,
  latencyMs: 80,
  blockNumber: 123,
  blockAgeS: 2,
  blockTimeS: 2,
  baseFeeGwei: 0.01,
  priorityFeeGwei: 0.001,
  error: null,
  ...extra,
})

beforeEach(() => {
  rpcCall.mockReset()
  rpcCall.mockImplementation(async (method: string) => {
    if (method === 'trading.allowances.list')
      return { wallet: WALLET.address, chainId: null, count: 2, unlimitedCount: 2, allowances: [] }
    if (method === 'trading.network')
      return {
        checkedAt: 1,
        chains: [
          chain({}),
          chain({
            chainId: 4663,
            key: 'robinhood',
            name: 'Robinhood Chain',
            healthy: false,
            blockAgeS: 90,
          }),
        ],
      }
    return {}
  })
})

describe('ToolsPicker', () => {
  it('lays the tools out in groups, badges the read-only ones, and shows live facts', async () => {
    const onPick = vi.fn()
    renderDesk(<ToolsPicker wallet={WALLET.address} onPick={onPick} onClose={vi.fn()} />)
    const picker = screen.getByTestId('tools-picker')
    expect(picker).toHaveTextContent('Move money')
    expect(picker).toHaveTextContent('Safety')
    expect(picker).toHaveTextContent('Watch')
    for (const id of ['send', 'multisend', 'allowances', 'inspect', 'network'])
      expect(screen.getByTestId(`tool-${id}`)).toBeInTheDocument()
    expect(screen.getByTestId('tool-inspect')).toHaveTextContent('No trades')
    expect(screen.getByTestId('tool-send')).not.toHaveTextContent('No trades')
    await waitFor(() =>
      expect(screen.getByTestId('tool-allowances-live')).toHaveTextContent(
        '2 unlimited allowances',
      ),
    )
    expect(screen.getByTestId('tool-allowances')).toHaveAttribute('data-tone', 'warn')
    expect(screen.getByTestId('tool-network-live')).toHaveTextContent('Robinhood Chain · Behind')
    fireEvent.click(screen.getByTestId('tool-multisend'))
    expect(onPick).toHaveBeenCalledWith('multisend')
  })
})

describe('NetworkSheet', () => {
  it('lists every chain with its state and numbers', async () => {
    renderDesk(<NetworkSheet onBack={vi.fn()} onClose={vi.fn()} />)
    await waitFor(() => expect(screen.getByTestId('network-sheet')).toBeInTheDocument())
    const rows = screen.getAllByRole('listitem')
    expect(rows[0]).toHaveTextContent('Base')
    expect(rows[0]).toHaveTextContent('Healthy')
    expect(rows[0]).toHaveTextContent('#123')
    expect(rows[0]).toHaveTextContent('0.0100 gwei')
    expect(rows[1]).toHaveTextContent('Behind')
    expect(rows[1]).toHaveTextContent('90 s')
  })
})
