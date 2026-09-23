import { fireEvent, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderDesk, WALLET } from '../test-utils'
import { ToolsPanel } from './ToolsPanel'

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
  rpcCall.mockImplementation(async (method: string) => {
    if (method === 'trading.allowances.list')
      return { wallet: WALLET.address, chainId: null, count: 1, unlimitedCount: 1, allowances: [] }
    if (method === 'trading.network')
      return {
        checkedAt: 1,
        chains: [
          {
            chainId: 8453,
            key: 'base',
            name: 'Base',
            native: 'ETH',
            rpcUrl: 'x',
            healthy: true,
            latencyMs: 80,
            blockNumber: 123,
            blockAgeS: 2,
            blockTimeS: 2,
            baseFeeGwei: 0.01,
            priorityFeeGwei: 0.001,
            error: null,
          },
          {
            chainId: 4663,
            key: 'robinhood',
            name: 'Robinhood Chain',
            native: 'ETH',
            rpcUrl: 'y',
            healthy: false,
            latencyMs: 300,
            blockNumber: 99,
            blockAgeS: 120,
            blockTimeS: 0.1,
            baseFeeGwei: null,
            priorityFeeGwei: null,
            error: null,
          },
        ],
      }
    return {}
  })
})

describe('ToolsPanel', () => {
  it('shows every tool with its live headline and opens each one', async () => {
    const onSend = vi.fn()
    const onInspect = vi.fn()
    renderDesk(
      <ToolsPanel wallet={WALLET.address} onSend={onSend} onInspect={onInspect} onBurn={vi.fn()} />,
    )
    await waitFor(() =>
      expect(screen.getByTestId('tool-allowances')).toHaveTextContent('1 unlimited allowance'),
    )
    expect(screen.getByTestId('tool-allowances')).toHaveAttribute('data-tone', 'warn')
    const network = screen.getByTestId('tool-network')
    expect(network).toHaveTextContent('Robinhood Chain · Behind')
    expect(network).toHaveTextContent('#123 · 2s · 0.0100 gwei · 80 ms')
    // Idle, the refresh button still has its arrow: never an empty box.
    await waitFor(() =>
      expect(screen.getByTestId('tool-network-refresh').querySelector('svg')).not.toBeNull(),
    )
    fireEvent.click(screen.getByTestId('tool-send'))
    expect(onSend).toHaveBeenCalled()
    fireEvent.click(screen.getByTestId('tool-inspect'))
    expect(onInspect).toHaveBeenCalled()
    fireEvent.click(screen.getByTestId('tool-allowances'))
    expect(screen.getByTestId('tools-back')).toBeInTheDocument()
    expect(screen.getByText('No live allowances')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('tools-back'))
    expect(screen.getByTestId('tool-send')).toBeInTheDocument()
  })

  it('carries the same cards as the picker, Multisend and Burn included', async () => {
    const onSend = vi.fn()
    const onMultisend = vi.fn()
    const onBurn = vi.fn()
    renderDesk(
      <ToolsPanel
        wallet={WALLET.address}
        onSend={onSend}
        onMultisend={onMultisend}
        onInspect={vi.fn()}
        onBurn={onBurn}
      />,
    )
    for (const id of ['send', 'multisend', 'allowances', 'inspect', 'network', 'burn']) {
      expect(screen.getByTestId(`tool-${id}`)).toBeInTheDocument()
    }
    fireEvent.click(screen.getByTestId('tool-multisend'))
    expect(onMultisend).toHaveBeenCalled()
    expect(onSend).not.toHaveBeenCalled()
    // Burn wears its warning without waiting for any live read.
    expect(screen.getByTestId('tool-burn')).toHaveAttribute('data-tone', 'danger')
    fireEvent.click(screen.getByTestId('tool-burn'))
    expect(onBurn).toHaveBeenCalled()
    await waitFor(() => expect(screen.getByTestId('tool-network')).toHaveTextContent('Base'))
  })
})
