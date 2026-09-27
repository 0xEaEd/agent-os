import { fireEvent, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderDesk, USDC, WALLET } from '../test-utils'
import type { Token } from '../types'
import { BurnSheet } from './BurnSheet'
import { BURN_ADDRESS } from './desk-logic'

const rpcCall = vi.fn()
vi.mock('@/app/providers', () => ({
  useRpc: () => ({ call: rpcCall, waitForConnection: async () => {}, on: () => () => {} }),
}))
vi.mock('~/lib/desktop-api', () => ({
  desktopApi: () => ({ app: { openExternal: vi.fn(async () => {}) } }),
  isDesktop: () => true,
}))
const toasts = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn() }))
vi.mock('sonner', () => ({ toast: toasts }))

const SPAM: Token = {
  chainId: 8453,
  address: '0x4444444444444444444444444444444444444444',
  symbol: 'SPAM',
  name: 'Free Claim',
  decimals: 18,
  logoUrl: null,
  native: false,
  verified: false,
}
const ETH: Token = {
  chainId: 8453,
  address: '0x0000000000000000000000000000000000000000',
  symbol: 'ETH',
  name: 'Ether',
  decimals: 18,
  logoUrl: null,
  native: true,
  verified: true,
}

const BALANCES = {
  balances: [
    {
      chainId: 8453,
      token: ETH,
      raw: '500000000000000000',
      amount: '0.5',
      priceUsd: 3000,
      valueUsd: 1500,
      change24hPct: 0,
    },
    {
      chainId: 8453,
      token: USDC,
      raw: '15000000',
      amount: '15',
      priceUsd: 1,
      valueUsd: 15,
      change24hPct: 0,
    },
    {
      chainId: 8453,
      token: SPAM,
      raw: '1000000000000000000000000',
      amount: '1000000',
      priceUsd: 0,
      valueUsd: 0,
      change24hPct: null,
      hidden: true,
    },
  ],
}

const sendCalls = () => rpcCall.mock.calls.filter((c) => c[0] === 'trading.send')

beforeEach(() => {
  rpcCall.mockReset()
  toasts.success.mockReset()
  toasts.error.mockReset()
  toasts.warning.mockReset()
  rpcCall.mockImplementation(async (method: string) =>
    method === 'wallet.balances' ? BALANCES : {},
  )
})

function mount(onClose = vi.fn(), onAsk = vi.fn()) {
  renderDesk(
    <BurnSheet wallets={[WALLET]} primary={WALLET.address} onClose={onClose} onAsk={onAsk} />,
  )
  return { onClose, onAsk }
}

/** Pick the token, size it, and type the symbol back. */
async function fill(symbol = 'SPAM', amount = '1000000') {
  const address = symbol === 'SPAM' ? SPAM.address : USDC.address
  await waitFor(() => expect(screen.getByTestId('burn-token')).toHaveTextContent(symbol))
  fireEvent.change(screen.getByTestId('burn-token'), { target: { value: address } })
  fireEvent.change(screen.getByTestId('burn-amount'), { target: { value: amount } })
  fireEvent.change(screen.getByTestId('burn-confirm'), { target: { value: symbol } })
}

describe('BurnSheet', () => {
  it('asks for hidden balances and never offers the native coin', async () => {
    mount()
    await waitFor(() => expect(screen.getByTestId('burn-token')).toHaveTextContent('SPAM'))
    // The junk is why this sheet exists, so it is in the list and labelled.
    expect(screen.getByTestId('burn-token')).toHaveTextContent('hidden as junk')
    // ETH is not burnable here at all: it is not even an option to click past.
    expect(screen.getByTestId('burn-token')).not.toHaveTextContent('ETH')
    const read = rpcCall.mock.calls.find((c) => c[0] === 'wallet.balances')
    expect(read?.[1]).toMatchObject({ includeHidden: true })
  })

  it('will not burn until the symbol is typed back', async () => {
    const { onAsk } = mount()
    await waitFor(() => expect(screen.getByTestId('burn-token')).toHaveTextContent('SPAM'))
    fireEvent.change(screen.getByTestId('burn-token'), { target: { value: SPAM.address } })
    fireEvent.change(screen.getByTestId('burn-amount'), { target: { value: '1000000' } })
    fireEvent.click(screen.getByTestId('burn-ask'))
    expect(onAsk).not.toHaveBeenCalled()
    expect(screen.getByTestId('burn-error')).toHaveTextContent('symbol does not match')
    // The wrong symbol is no better than none.
    fireEvent.change(screen.getByTestId('burn-confirm'), { target: { value: 'USDC' } })
    fireEvent.click(screen.getByTestId('burn-ask'))
    expect(onAsk).not.toHaveBeenCalled()
    fireEvent.change(screen.getByTestId('burn-confirm'), { target: { value: 'spam' } })
    fireEvent.click(screen.getByTestId('burn-ask'))
    expect(onAsk).toHaveBeenCalledTimes(1)
  })

  it('refuses more than the wallet holds', async () => {
    mount()
    await fill('SPAM', '2000000')
    fireEvent.click(screen.getByTestId('burn-ask'))
    expect(screen.getByTestId('burn-error')).toHaveTextContent('more than the wallet holds')
    expect(sendCalls()).toHaveLength(0)
  })

  it('argues against burning something that is still worth money', async () => {
    mount()
    await fill('USDC', '15')
    expect(screen.getByTestId('burn-valuable')).toHaveTextContent('$15')
    expect(screen.getByTestId('burn-valuable')).toHaveTextContent('a swap would return that value')
  })

  it('changing the token clears the amount and the confirmation', async () => {
    mount()
    await fill('SPAM', '1000000')
    fireEvent.change(screen.getByTestId('burn-token'), { target: { value: USDC.address } })
    expect(screen.getByTestId('burn-amount')).toHaveValue('')
    expect(screen.getByTestId('burn-confirm')).toHaveValue('')
  })

  it('asks the desk with a prompt pinned to the burn address', async () => {
    const { onAsk } = mount()
    await fill()
    fireEvent.click(screen.getByTestId('burn-ask'))
    const prompt = onAsk.mock.calls[0]![0] as string
    // Ungrouped: this number is copied into `--amount`, where a comma is not a digit.
    expect(prompt).toContain('Burn 1000000 SPAM')
    expect(prompt).not.toContain('1,000,000')
    expect(prompt).toContain(BURN_ADDRESS)
    expect(prompt).toContain('--client-id')
    expect(sendCalls()).toHaveLength(0)
  })

  it('burns only on the second click, to the burn address, as the user', async () => {
    rpcCall.mockImplementation(async (method: string) =>
      method === 'trading.send'
        ? { orders: [{ orderId: 'a', status: 'submitted' }], batchId: null }
        : BALANCES,
    )
    const { onClose } = mount()
    await fill()
    fireEvent.click(screen.getByTestId('burn-now'))
    expect(sendCalls()).toHaveLength(0)
    expect(screen.getByTestId('burn-review')).toHaveTextContent('1,000,000 SPAM')
    expect(screen.getByTestId('burn-review')).toHaveTextContent('the whole balance')
    fireEvent.click(screen.getByTestId('burn-now'))
    await waitFor(() => expect(sendCalls()).toHaveLength(1))
    expect(sendCalls()[0]![1]).toMatchObject({
      chainId: 8453,
      wallet: WALLET.address,
      token: SPAM.address,
      recipients: [{ to: BURN_ADDRESS, amount: '1000000' }],
      initiator: 'manual',
    })
    await waitFor(() => expect(onClose).toHaveBeenCalled())
  })

  it('points at hiding when the token blocks transfers', async () => {
    rpcCall.mockImplementation(async (method: string) =>
      method === 'trading.send'
        ? { orders: [{ orderId: 'a', status: 'failed', reason: 'execution reverted' }] }
        : BALANCES,
    )
    const { onClose } = mount()
    await fill()
    fireEvent.click(screen.getByTestId('burn-now'))
    fireEvent.click(screen.getByTestId('burn-now'))
    await waitFor(() => expect(toasts.error).toHaveBeenCalled())
    expect(toasts.error.mock.calls[0]![1]).toMatchObject({
      description: expect.stringContaining('hide it instead'),
    })
    // The sheet stays open so the next move is still in reach.
    expect(onClose).not.toHaveBeenCalled()
  })
})
