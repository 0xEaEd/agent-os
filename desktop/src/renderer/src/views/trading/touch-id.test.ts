import { readFileSync } from 'node:fs'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AuthResult } from '@shared/app'
import { DEFAULT_SETTINGS } from '@shared/settings'
import { resetBiometricGateForTests, TouchIdDeclined } from '~/lib/biometric-gate'
import { desktopApi } from '~/lib/desktop-api'
import { useSettings } from '~/stores/settings'
import { order, USDC } from './test-utils'
import {
  mandateCeilingUsd,
  mandateTouchId,
  orderTouchId,
  requireMandateTouchId,
  vaultReason,
} from './touch-id'
import type { Mandate, MandatePayload } from './types'

vi.mock('sonner', () => ({ toast: { info: vi.fn(), error: vi.fn() } }))

const MANDATE = (
  JSON.parse(
    readFileSync('src/renderer/src/views/trading/desk/__fixtures__/dca/mandate.json', 'utf8'),
  ) as MandatePayload
).mandate

function mandate(extra: Partial<Mandate> = {}): Mandate {
  return { ...MANDATE, ...extra }
}

describe('orderTouchId', () => {
  it('names a swap the way the card does, on its chain', () => {
    const ask = orderTouchId(order({ amountIn: '0.05', valueUsd: 120 }))
    expect(ask).toEqual({ kind: 'approve', reason: 'approve 0.05 ETH → USDC on Base' })
  })

  it('takes the card’s risk: value, impact, an unpriced send', () => {
    expect(orderTouchId(order({ valueUsd: 900 })).kind).toBe('approve-high')
    expect(orderTouchId(order({ valueUsd: 20, priceImpactPct: 12 })).kind).toBe('approve-high')
    const send = order({
      kind: 'send',
      tokenIn: USDC,
      amountIn: '25',
      valueUsd: null,
      recipient: '0x6c83a1b2c3d4e5f60718293a4b5c6d7e8f908312',
    })
    expect(orderTouchId(send)).toEqual({
      kind: 'approve-high',
      reason: 'approve send 25 USDC → 0x6c83…8312 on Base',
    })
  })

  it('reads a multisend as one ask: the batch total decides the risk', () => {
    const legs = [1, 2, 3].map((n) =>
      order({
        orderId: `o${n}`,
        kind: 'send',
        batchId: 'b1',
        tokenIn: USDC,
        amountIn: '200',
        valueUsd: 200,
        recipient: `0x${String(n).repeat(40)}`,
      }),
    )
    const ask = orderTouchId(legs[0]!, legs)
    expect(ask.kind).toBe('approve-high')
    expect(ask.reason).toBe('approve multisend 600 USDC → 3 recipients on Base')
    // One leg whose siblings are unknown is not taken for small.
    expect(orderTouchId({ ...legs[0]!, valueUsd: 1 }).kind).toBe('approve-high')
  })
})

describe('mandateTouchId', () => {
  it('reads what the mandate may spend: its cap, else buys × USD per buy', () => {
    expect(mandateCeilingUsd(mandate({ budget: { ...MANDATE.budget, capUsd: 120 } }))).toBe(120)
    expect(
      mandateCeilingUsd(
        mandate({
          budget: { ...MANDATE.budget, capUsd: 0, usdPerRun: 25 },
          runs: { ...MANDATE.runs, max: 4 },
        }),
      ),
    ).toBe(100)
    expect(
      mandateCeilingUsd(
        mandate({
          budget: { ...MANDATE.budget, capUsd: 0 },
          runs: { ...MANDATE.runs, max: null },
        }),
      ),
    ).toBeNull()
  })

  it('names the mandate and takes the high-risk line from what it may spend', () => {
    const small = mandateTouchId(
      mandate({ name: 'DCA ETH', budget: { ...MANDATE.budget, capUsd: 50 } }),
    )
    expect(small.kind).toBe('approve')
    expect(small.reason).toBe(`approve the DCA “DCA ETH”, up to $50.00, on ${MANDATE.chain.name}`)
    expect(mandateTouchId(mandate({ budget: { ...MANDATE.budget, capUsd: 5_000 } })).kind).toBe(
      'approve-high',
    )
  })
})

describe('vaultReason', () => {
  it('names the wallet by label and short address', () => {
    const w = { address: '0x1234567890abcdef1234567890abcdef1234abcd', label: 'Main' }
    expect(vaultReason('export', w)).toBe('export the private key of Main (0x1234…abcd)')
    expect(vaultReason('exportKeystore', w)).toBe('export the keystore of Main (0x1234…abcd)')
    expect(vaultReason('remove', { ...w, label: ' ' })).toBe('remove the wallet 0x1234…abcd')
  })
})

describe('requireMandateTouchId', () => {
  beforeEach(() => {
    resetBiometricGateForTests()
    useSettings.setState({
      loaded: true,
      settings: { ...structuredClone(DEFAULT_SETTINGS), security: { touchId: 'all' } },
    })
  })

  it('reads the mandate to name it, then prompts; a cancel throws', async () => {
    const authenticate = vi
      .spyOn(desktopApi().app, 'authenticate')
      .mockResolvedValue({ ok: false, reason: 'cancelled' } satisfies AuthResult)
    const call = vi.fn(async () => ({ kind: 'mandate', mandate: mandate({ name: 'DCA ETH' }) }))
    await expect(requireMandateTouchId(call, 'dca_1')).rejects.toBeInstanceOf(TouchIdDeclined)
    expect(call).toHaveBeenCalledWith('trading.dca.get', { mandateId: 'dca_1' })
    expect(authenticate.mock.calls[0]?.[0]).toMatch(/^approve the DCA “DCA ETH”/)
    authenticate.mockRestore()
  })

  it('still prompts, as high-risk, when the mandate cannot be read', async () => {
    useSettings.setState({
      settings: { ...structuredClone(DEFAULT_SETTINGS), security: { touchId: 'high' } },
    })
    const authenticate = vi
      .spyOn(desktopApi().app, 'authenticate')
      .mockResolvedValue({ ok: true } satisfies AuthResult)
    const call = vi.fn(async () => {
      throw new Error('offline')
    })
    await expect(requireMandateTouchId(call, 'dca_1')).resolves.toBeUndefined()
    expect(authenticate).toHaveBeenCalledWith('approve the DCA dca_1')
    authenticate.mockRestore()
  })
})
