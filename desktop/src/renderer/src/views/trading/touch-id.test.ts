import { readFileSync } from 'node:fs'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AuthResult } from '@shared/app'
import { DEFAULT_SETTINGS } from '@shared/settings'
import { resetBiometricGateForTests, TouchIdDeclined } from '~/lib/biometric-gate'
import { desktopApi } from '~/lib/desktop-api'
import { useSettings } from '~/stores/settings'
import { order, USDC } from './test-utils'
import {
  bracketTouchId,
  mandateCeilingUsd,
  mandateTouchId,
  orderTouchId,
  requireBracketTouchId,
  requireMandateTouchId,
  requireTriggerTouchId,
  triggerTouchId,
  vaultReason,
} from './touch-id'
import type {
  Bracket,
  BracketPayload,
  Mandate,
  MandatePayload,
  Trigger,
  TriggerPayload,
} from './types'

vi.mock('sonner', () => ({ toast: { info: vi.fn(), error: vi.fn() } }))

const MANDATE = (
  JSON.parse(
    readFileSync('src/renderer/src/views/trading/desk/__fixtures__/dca/mandate.json', 'utf8'),
  ) as MandatePayload
).mandate

function mandate(extra: Partial<Mandate> = {}): Mandate {
  return { ...MANDATE, ...extra }
}

const TRIGGER = (
  JSON.parse(
    readFileSync('src/renderer/src/views/trading/desk/__fixtures__/trigger/trigger.json', 'utf8'),
  ) as TriggerPayload
).trigger

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

describe('triggerTouchId', () => {
  it('names the trigger and what it does; high-risk from what one fire moves', () => {
    const ask = triggerTouchId(TRIGGER)
    expect(ask).toEqual({
      kind: 'approve',
      reason: 'arm the trigger “Stop-loss ETH”: sell 50 % of ETH when under $3,800, on Base',
    })
    const big = { ...TRIGGER, action: { ...TRIGGER.action, estimatedUsd: 900 } }
    expect(triggerTouchId(big).kind).toBe('approve-high')
    const unknown = { ...TRIGGER, action: { ...TRIGGER.action, estimatedUsd: null } }
    expect(triggerTouchId(unknown).kind).toBe('approve-high')
    // An alert moves nothing.
    const alert: Trigger = {
      ...TRIGGER,
      kind: 'alert',
      action: { ...TRIGGER.action, kind: 'alert', amountPct: null, estimatedUsd: null },
    }
    expect(triggerTouchId(alert).kind).toBe('approve')
  })
})

describe('requireTriggerTouchId', () => {
  beforeEach(() => {
    resetBiometricGateForTests()
    useSettings.setState({
      loaded: true,
      settings: { ...structuredClone(DEFAULT_SETTINGS), security: { touchId: 'all' } },
    })
  })

  it('reads the trigger to name it, and asks as high-risk when it cannot', async () => {
    const authenticate = vi
      .spyOn(desktopApi().app, 'authenticate')
      .mockResolvedValue({ ok: true } satisfies AuthResult)
    const call = vi.fn(async () => ({ kind: 'trigger', trigger: TRIGGER }))
    await requireTriggerTouchId(call, 'trg_1a2b3c4d')
    expect(call).toHaveBeenCalledWith('trading.trigger.get', { triggerId: 'trg_1a2b3c4d' })
    expect(authenticate.mock.calls[0]?.[0]).toMatch(/^arm the trigger “Stop-loss ETH”/)
    const failing = vi.fn(async () => {
      throw new Error('trading.trigger.not_found')
    })
    await requireTriggerTouchId(failing, 'trg_9')
    expect(authenticate.mock.calls[1]?.[0]).toBe('arm the trigger trg_9')
    authenticate.mockRestore()
  })
})

const BRACKET = (
  JSON.parse(
    readFileSync('src/renderer/src/views/trading/desk/__fixtures__/bracket/bracket.json', 'utf8'),
  ) as BracketPayload
).bracket

describe('bracketTouchId', () => {
  it('names both lines and the size; high-risk from what the stop would move; an alert moves nothing', () => {
    expect(bracketTouchId(BRACKET)).toEqual({
      kind: 'approve',
      reason:
        'arm the bracket “Protect ETH”: sell 100 % of ETH · take profit over $4,560 · stop under $3,420, on Base',
    })
    expect(bracketTouchId(BRACKET, 'fire').reason).toMatch(/^fire the bracket “Protect ETH” now: /)
    const big = { ...BRACKET, action: { ...BRACKET.action, estimatedUsd: 900 } }
    expect(bracketTouchId(big).kind).toBe('approve-high')
    const unknown = { ...BRACKET, action: { ...BRACKET.action, estimatedUsd: null } }
    expect(bracketTouchId(unknown).kind).toBe('approve-high')
    const alert: Bracket = {
      ...BRACKET,
      kind: 'alert',
      action: { ...BRACKET.action, kind: 'alert', amountPct: null, estimatedUsd: null },
    }
    expect(bracketTouchId(alert).kind).toBe('approve')
  })
})

describe('requireBracketTouchId', () => {
  beforeEach(() => {
    resetBiometricGateForTests()
    useSettings.setState({
      loaded: true,
      settings: { ...structuredClone(DEFAULT_SETTINGS), security: { touchId: 'all' } },
    })
  })

  it('reads the bracket to name it, and asks as high-risk when it cannot', async () => {
    const authenticate = vi
      .spyOn(desktopApi().app, 'authenticate')
      .mockResolvedValue({ ok: true } satisfies AuthResult)
    const call = vi.fn(async () => ({ kind: 'bracket', bracket: BRACKET }))
    await requireBracketTouchId(call, 'brk_1a2b3c4d')
    expect(call).toHaveBeenCalledWith('trading.bracket.get', { bracketId: 'brk_1a2b3c4d' })
    expect(authenticate.mock.calls[0]?.[0]).toMatch(/^arm the bracket “Protect ETH”/)
    await requireBracketTouchId(call, 'brk_1a2b3c4d', 'fire')
    expect(authenticate.mock.calls[1]?.[0]).toMatch(/^fire the bracket “Protect ETH” now/)
    const failing = vi.fn(async () => {
      throw new Error('trading.bracket.not_found')
    })
    await requireBracketTouchId(failing, 'brk_9')
    expect(authenticate.mock.calls[2]?.[0]).toBe('arm the bracket brk_9')
    authenticate.mockRestore()
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
