import { fireEvent, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderDesk, USDC, WALLET } from '../test-utils'
import { SendSheet } from './SendSheet'

const rpcCall = vi.fn()
vi.mock('@/app/providers', () => ({
  useRpc: () => ({ call: rpcCall, waitForConnection: async () => {}, on: () => () => {} }),
}))
vi.mock('~/lib/desktop-api', () => ({
  desktopApi: () => ({ app: { openExternal: vi.fn(async () => {}) } }),
  isDesktop: () => true,
}))
const toasts = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
}))
vi.mock('sonner', () => ({ toast: toasts }))

const A = '0x2222222222222222222222222222222222222222'
const B = '0x3333333333333333333333333333333333333333'
// An EIP-55 vector, and the same with one letter's case flipped.
const GOOD = '0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed'
const MANGLED = '0x5aaeb6053F3E94C9b9A09f33669435E7Ef1BeAed'

const BALANCES = {
  balances: [
    {
      chainId: 8453,
      token: USDC,
      raw: '15000000',
      amount: '15',
      priceUsd: 1,
      valueUsd: 15,
      change24hPct: 0,
    },
  ],
}

const sendCalls = () => rpcCall.mock.calls.filter((c) => c[0] === 'trading.send')

beforeEach(() => {
  rpcCall.mockReset()
  toasts.success.mockReset()
  toasts.error.mockReset()
  toasts.warning.mockReset()
})

function fill() {
  fireEvent.change(screen.getByTestId('send-token'), { target: { value: 'USDC' } })
  fireEvent.change(screen.getByTestId('send-recipients'), {
    target: { value: `${A}\n${B}=2.5` },
  })
  fireEvent.change(screen.getByTestId('send-amount'), { target: { value: '10' } })
}

function mount(onClose = vi.fn(), onAsk = vi.fn()) {
  renderDesk(
    <SendSheet wallets={[WALLET]} primary={WALLET.address} onClose={onClose} onAsk={onAsk} />,
  )
  return { onClose, onAsk }
}

describe('SendSheet', () => {
  it('refuses to ask or send until the form is whole', () => {
    rpcCall.mockResolvedValue({})
    const { onAsk } = mount()
    fireEvent.click(screen.getByTestId('send-ask'))
    expect(onAsk).not.toHaveBeenCalled()
    expect(screen.getByTestId('send-error')).toHaveTextContent('Name the token')
    fireEvent.change(screen.getByTestId('send-token'), { target: { value: 'USDC' } })
    fireEvent.change(screen.getByTestId('send-recipients'), { target: { value: 'vitalik.eth' } })
    fireEvent.click(screen.getByTestId('send-ask'))
    expect(screen.getByTestId('send-error')).toHaveTextContent('not an address')
    expect(sendCalls()).toHaveLength(0)
  })

  it('asks the desk with an exact prompt the preview also shows', () => {
    rpcCall.mockResolvedValue({})
    const { onAsk } = mount()
    fill()
    expect(screen.getByTestId('send-sheet')).toHaveTextContent('2 recipients')
    fireEvent.click(screen.getByTestId('send-preview-toggle'))
    const preview = screen.getByTestId('send-preview').textContent ?? ''
    expect(preview).toContain(`- ${A} → 10 USDC`)
    expect(preview).toContain(`- ${B} → 2.5 USDC`)
    fireEvent.click(screen.getByTestId('send-ask'))
    expect(onAsk).toHaveBeenCalledTimes(1)
    expect(onAsk.mock.calls[0]![0]).toBe(preview)
    expect(sendCalls()).toHaveLength(0)
  })

  it('sends now only on the second click, as the user, with each leg sized', async () => {
    rpcCall.mockImplementation(async (method: string) =>
      method === 'trading.send'
        ? { orders: [{ orderId: 'a', status: 'submitted' }], batchId: 'bat_1' }
        : BALANCES,
    )
    const { onClose } = mount()
    fill()
    const now = screen.getByTestId('send-now')
    fireEvent.click(now)
    expect(now).toHaveTextContent('Click again to send')
    expect(sendCalls()).toHaveLength(0)
    fireEvent.click(now)
    await waitFor(() => expect(sendCalls()).toHaveLength(1))
    expect(rpcCall).toHaveBeenCalledWith('trading.send', {
      chainId: 8453,
      wallet: WALLET.address,
      token: 'USDC',
      recipients: [
        { to: A, amount: '10' },
        { to: B, amount: '2.5' },
      ],
      initiator: 'manual',
    })
    await waitFor(() => expect(onClose).toHaveBeenCalled())
  })

  it('writes out what the second click will send: wallet, token, every leg, the total', async () => {
    rpcCall.mockResolvedValue(BALANCES)
    mount()
    fill()
    await screen.findByTestId('send-balance')
    expect(screen.queryByTestId('send-review')).toBeNull()
    fireEvent.click(screen.getByTestId('send-now'))
    const review = screen.getByTestId('send-review')
    expect(review).toHaveTextContent('Base')
    expect(review).toHaveTextContent('Main')
    expect(review).toHaveTextContent('0x1111…1111')
    expect(review).toHaveTextContent(A)
    expect(review).toHaveTextContent(B)
    expect(review).toHaveTextContent('10 USDC')
    expect(review).toHaveTextContent('2.5 USDC')
    expect(review).toHaveTextContent('12.5 USDC')
    expect(review).toHaveTextContent('$12.50')
  })

  it('shows the balance of the typed token and says when the list outspends it', async () => {
    rpcCall.mockResolvedValue(BALANCES)
    mount()
    fill()
    const balance = await screen.findByTestId('send-balance')
    expect(balance).toHaveTextContent('Balance: 15 USDC')
    expect(balance).not.toHaveTextContent('More than this wallet holds')
    fireEvent.change(screen.getByTestId('send-amount'), { target: { value: '20' } })
    expect(screen.getByTestId('send-balance')).toHaveTextContent('More than this wallet holds')
  })

  it('refuses a mixed-case address whose checksum does not match', () => {
    rpcCall.mockResolvedValue({})
    const { onAsk } = mount()
    fireEvent.change(screen.getByTestId('send-token'), { target: { value: 'USDC' } })
    fireEvent.change(screen.getByTestId('send-amount'), { target: { value: '1' } })
    fireEvent.change(screen.getByTestId('send-recipients'), { target: { value: MANGLED } })
    fireEvent.click(screen.getByTestId('send-ask'))
    expect(onAsk).not.toHaveBeenCalled()
    expect(screen.getByTestId('send-error')).toHaveTextContent('Checksum does not match')
    fireEvent.click(screen.getByTestId('send-now'))
    expect(screen.queryByTestId('send-review')).toBeNull()
    // The correctly cased address passes.
    fireEvent.change(screen.getByTestId('send-recipients'), { target: { value: GOOD } })
    fireEvent.click(screen.getByTestId('send-ask'))
    expect(onAsk).toHaveBeenCalledTimes(1)
  })

  it('keeps the sheet open and warns when only some legs failed', async () => {
    rpcCall.mockImplementation(async (method: string) =>
      method === 'trading.send'
        ? {
            orders: [
              { orderId: 'a', status: 'submitted' },
              { orderId: 'b', status: 'failed', reason: 'insufficient gas' },
            ],
            batchId: 'bat_2',
          }
        : BALANCES,
    )
    const { onClose } = mount()
    fill()
    fireEvent.click(screen.getByTestId('send-now'))
    fireEvent.click(screen.getByTestId('send-now'))
    await waitFor(() => expect(toasts.warning).toHaveBeenCalled())
    expect(toasts.warning.mock.calls[0]![0]).toContain('1 of 2')
    expect(onClose).not.toHaveBeenCalled()
    expect(toasts.success).not.toHaveBeenCalled()
  })

  it('cannot be dismissed while the send is in flight', async () => {
    let resolve: (v: unknown) => void = () => {}
    rpcCall.mockImplementation((method: string) =>
      method === 'trading.send'
        ? new Promise((r) => {
            resolve = r
          })
        : Promise.resolve(BALANCES),
    )
    const { onClose } = mount()
    fill()
    fireEvent.click(screen.getByTestId('send-now'))
    fireEvent.click(screen.getByTestId('send-now'))
    await waitFor(() => expect(screen.getByTestId('send-now')).toHaveTextContent('Sending…'))
    expect(screen.getByText('Cancel')).toBeDisabled()
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
    // The footer Cancel and the header X both answer to the same name.
    for (const b of screen.getAllByRole('button', { name: 'Cancel' })) fireEvent.click(b)
    expect(onClose).not.toHaveBeenCalled()
    resolve({ orders: [{ orderId: 'a', status: 'submitted' }], batchId: null })
    await waitFor(() => expect(onClose).toHaveBeenCalled())
  })
})
