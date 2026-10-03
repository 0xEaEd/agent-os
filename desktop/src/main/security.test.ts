// @vitest-environment node
import { describe, expect, it, vi } from 'vitest'

// The module only reaches for systemPreferences as a default argument; every
// test hands it a backend of its own.
vi.mock('electron', () => ({ systemPreferences: {} }))

import { authenticate, authFailureReason, biometricsAvailable, isValidAuthReason } from './security'

describe('authFailureReason', () => {
  it('reads a dismissed sheet as cancelled', () => {
    for (const message of [
      'Canceled by user.',
      'User canceled',
      'Authentication was cancelled',
      'Canceled by system.',
      'Fallback authentication mechanism selected.',
    ]) {
      expect(authFailureReason(new Error(message)), message).toBe('cancelled')
    }
  })

  it('reads a sensor that cannot be used now as unavailable', () => {
    for (const message of [
      'Biometry is not available on this device.',
      'Touch ID is not available',
      'No fingers are enrolled with Touch ID.',
      'No identities are enrolled.',
      'Biometry is not enrolled.',
      'Passcode not set.',
      'Biometry is locked out.',
      'User interaction is required.',
    ]) {
      expect(authFailureReason(new Error(message)), message).toBe('unavailable')
    }
  })

  it('reads anything else, and anything unreadable, as failed', () => {
    expect(authFailureReason(new Error('Application retry limit exceeded.'))).toBe('failed')
    expect(authFailureReason(new Error('Authentication failed.'))).toBe('failed')
    expect(authFailureReason(new Error('Authentication context invalidated.'))).toBe('failed')
    expect(authFailureReason(new Error(''))).toBe('failed')
    expect(authFailureReason(undefined)).toBe('failed')
    expect(authFailureReason({ message: 'Canceled by user.' })).toBe('failed')
    // A bare string rejection is still read.
    expect(authFailureReason('CANCELED BY USER')).toBe('cancelled')
  })
})

describe('isValidAuthReason', () => {
  it('takes a short non-blank string only', () => {
    expect(isValidAuthReason('approve 0.05 ETH → USDC on Base')).toBe(true)
    expect(isValidAuthReason('x'.repeat(120))).toBe(true)
    expect(isValidAuthReason('x'.repeat(121))).toBe(false)
    expect(isValidAuthReason('   ')).toBe(false)
    expect(isValidAuthReason('')).toBe(false)
    expect(isValidAuthReason(42)).toBe(false)
    expect(isValidAuthReason(null)).toBe(false)
  })
})

describe('biometricsAvailable', () => {
  it('is what canPromptTouchID says, and false when it is missing or throws', () => {
    expect(biometricsAvailable({ canPromptTouchID: () => true })).toBe(true)
    expect(biometricsAvailable({ canPromptTouchID: () => false })).toBe(false)
    expect(biometricsAvailable({})).toBe(false)
    expect(
      biometricsAvailable({
        canPromptTouchID: () => {
          throw new Error('nope')
        },
      }),
    ).toBe(false)
  })
})

describe('authenticate', () => {
  it('resolves ok when the fingerprint matched, with the reason shown as given', async () => {
    const promptTouchID = vi.fn(async () => {})
    await expect(
      authenticate('approve 0.05 ETH → USDC on Base', {
        canPromptTouchID: () => true,
        promptTouchID,
      }),
    ).resolves.toEqual({ ok: true })
    expect(promptTouchID).toHaveBeenCalledWith('approve 0.05 ETH → USDC on Base')
  })

  it('maps a rejection to its reason', async () => {
    const backend = {
      canPromptTouchID: () => true,
      promptTouchID: vi.fn(async () => {
        throw new Error('Canceled by user.')
      }),
    }
    await expect(authenticate('test', backend)).resolves.toEqual({
      ok: false,
      reason: 'cancelled',
    })
  })

  it('never prompts without a sensor or with a bad reason', async () => {
    const promptTouchID = vi.fn(async () => {})
    await expect(
      authenticate('test', { canPromptTouchID: () => false, promptTouchID }),
    ).resolves.toEqual({ ok: false, reason: 'unavailable' })
    await expect(
      authenticate('x'.repeat(121), { canPromptTouchID: () => true, promptTouchID }),
    ).resolves.toEqual({ ok: false, reason: 'failed' })
    await expect(
      authenticate({ toString: () => 'test' }, { canPromptTouchID: () => true, promptTouchID }),
    ).resolves.toEqual({ ok: false, reason: 'failed' })
    expect(promptTouchID).not.toHaveBeenCalled()
  })

  it('refuses a second prompt while the sheet is up', async () => {
    let finish: () => void = () => {}
    const promptTouchID = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          finish = resolve
        }),
    )
    const backend = { canPromptTouchID: () => true, promptTouchID }
    const first = authenticate('first', backend)
    await expect(authenticate('second', backend)).resolves.toEqual({
      ok: false,
      reason: 'cancelled',
    })
    finish()
    await expect(first).resolves.toEqual({ ok: true })
    expect(promptTouchID).toHaveBeenCalledTimes(1)
    // And the next one prompts again.
    const third = authenticate('third', backend)
    finish()
    await expect(third).resolves.toEqual({ ok: true })
  })
})
