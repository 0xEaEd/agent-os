import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AuthResult } from '@shared/app'
import { DEFAULT_SETTINGS, type TouchIdMode } from '@shared/settings'

const toastFn = { info: vi.fn(), error: vi.fn(), success: vi.fn(), warning: vi.fn() }
vi.mock('sonner', () => ({ toast: toastFn }))

const authenticate = vi.fn<(reason: string) => Promise<AuthResult>>()
const settingsGet = vi.fn()
vi.mock('~/lib/desktop-api', () => ({
  desktopApi: () => ({ app: { authenticate }, settings: { get: settingsGet } }),
  isDesktop: () => true,
}))

const { useSettings } = await import('~/stores/settings')
const {
  biometricGate,
  clampReason,
  isTouchIdDeclined,
  needsTouchId,
  requireBiometric,
  resetBiometricGateForTests,
  TouchIdDeclined,
  useTouchIdPrompt,
} = await import('./biometric-gate')

function setMode(touchId: TouchIdMode): void {
  useSettings.setState({
    loaded: true,
    settings: { ...structuredClone(DEFAULT_SETTINGS), security: { touchId } },
  })
}

beforeEach(() => {
  resetBiometricGateForTests()
  authenticate.mockReset()
  settingsGet.mockReset()
  Object.values(toastFn).forEach((fn) => fn.mockClear())
})

describe('needsTouchId', () => {
  it('asks per mode: never, high-risk and vault, or everything', () => {
    expect(needsTouchId('off', 'approve')).toBe(false)
    expect(needsTouchId('off', 'approve-high')).toBe(false)
    expect(needsTouchId('off', 'vault')).toBe(false)
    expect(needsTouchId('high', 'approve')).toBe(false)
    expect(needsTouchId('high', 'approve-high')).toBe(true)
    expect(needsTouchId('high', 'vault')).toBe(true)
    expect(needsTouchId('all', 'approve')).toBe(true)
    expect(needsTouchId('all', 'approve-high')).toBe(true)
    expect(needsTouchId('all', 'vault')).toBe(true)
  })
})

describe('clampReason', () => {
  it('fits the reason in the 120 characters the sheet takes', () => {
    expect(clampReason('  approve  0.05 ETH\n→ USDC  ')).toBe('approve 0.05 ETH → USDC')
    const long = clampReason(`approve ${'x'.repeat(200)}`)
    expect(long).toHaveLength(120)
    expect(long.endsWith('…')).toBe(true)
  })
})

const OUTCOMES: { name: string; result: AuthResult; pass: boolean; toast: string | null }[] = [
  { name: 'ok', result: { ok: true }, pass: true, toast: null },
  {
    name: 'cancelled',
    result: { ok: false, reason: 'cancelled' },
    pass: false,
    toast: 'Approval cancelled — Touch ID was dismissed',
  },
  {
    name: 'unavailable',
    result: { ok: false, reason: 'unavailable' },
    pass: false,
    toast:
      'Touch ID is unavailable right now (closed lid?). Open the lid, or turn this off in Settings › Security',
  },
  {
    name: 'failed',
    result: { ok: false, reason: 'failed' },
    pass: false,
    toast: 'Touch ID did not confirm it is you. Nothing was sent.',
  },
]

function lastToast(): string | null {
  for (const fn of [toastFn.info, toastFn.error]) {
    const call = fn.mock.calls.at(-1)
    if (call) return String(call[0])
  }
  return null
}

describe('biometricGate', () => {
  describe('off', () => {
    for (const outcome of OUTCOMES) {
      it(`proceeds without prompting whatever Touch ID would say (${outcome.name})`, async () => {
        setMode('off')
        authenticate.mockResolvedValue(outcome.result)
        for (const kind of ['approve', 'approve-high', 'vault'] as const) {
          await expect(biometricGate(kind, 'approve 1 ETH → USDC on Base')).resolves.toBe(true)
        }
        expect(authenticate).not.toHaveBeenCalled()
        expect(lastToast()).toBeNull()
      })
    }
  })

  describe('high', () => {
    for (const outcome of OUTCOMES) {
      it(`prompts for a high-risk approval and a vault op only (${outcome.name})`, async () => {
        setMode('high')
        authenticate.mockResolvedValue(outcome.result)
        await expect(biometricGate('approve', 'approve 1 ETH → USDC on Base')).resolves.toBe(true)
        expect(authenticate).not.toHaveBeenCalled()
        await expect(biometricGate('approve-high', 'approve 900 USDC → ETH on Base')).resolves.toBe(
          outcome.pass,
        )
        expect(authenticate).toHaveBeenLastCalledWith('approve 900 USDC → ETH on Base')
        expect(lastToast()).toBe(outcome.toast)
        await expect(biometricGate('vault', 'remove the wallet 0x1234…abcd')).resolves.toBe(
          outcome.pass,
        )
        expect(authenticate).toHaveBeenCalledTimes(2)
      })
    }
  })

  describe('all', () => {
    for (const outcome of OUTCOMES) {
      it(`prompts for every kind (${outcome.name})`, async () => {
        setMode('all')
        authenticate.mockResolvedValue(outcome.result)
        for (const kind of ['approve', 'approve-high', 'vault'] as const) {
          await expect(biometricGate(kind, 'approve 1 ETH → USDC on Base')).resolves.toBe(
            outcome.pass,
          )
        }
        expect(authenticate).toHaveBeenCalledTimes(3)
        if (outcome.name === 'cancelled')
          // A vault op is not an approval: its toast says so.
          expect(toastFn.info).toHaveBeenLastCalledWith('Cancelled — Touch ID was dismissed', {
            id: 'touch-id',
          })
        else expect(lastToast()).toBe(outcome.toast)
      })
    }
  })

  it('reads a bridge that threw as a failure, never as a pass', async () => {
    setMode('all')
    authenticate.mockRejectedValue(new Error('IPC gone'))
    await expect(biometricGate('approve', 'approve x')).resolves.toBe(false)
    expect(toastFn.error).toHaveBeenCalledWith(
      'Touch ID did not confirm it is you. Nothing was sent.',
      { id: 'touch-id' },
    )
  })

  it('ignores a second call while the prompt is up, and says what the prompt is for', async () => {
    setMode('all')
    let answer: (r: AuthResult) => void = () => {}
    authenticate.mockImplementation(
      () =>
        new Promise<AuthResult>((resolve) => {
          answer = resolve
        }),
    )
    const first = biometricGate('approve', 'approve x', 'ord_1')
    await vi.waitFor(() => expect(authenticate).toHaveBeenCalledTimes(1))
    expect(useTouchIdPrompt.getState().key).toBe('ord_1')
    await expect(biometricGate('approve', 'approve x', 'ord_1')).resolves.toBe(false)
    expect(authenticate).toHaveBeenCalledTimes(1)
    expect(lastToast()).toBeNull()
    answer({ ok: true })
    await expect(first).resolves.toBe(true)
    expect(useTouchIdPrompt.getState().key).toBeNull()
  })

  it('loads the settings first when the store has not, instead of reading the default Off', async () => {
    useSettings.setState({ loaded: false, settings: structuredClone(DEFAULT_SETTINGS) })
    settingsGet.mockResolvedValue({
      ...structuredClone(DEFAULT_SETTINGS),
      security: { touchId: 'all' },
    })
    authenticate.mockResolvedValue({ ok: false, reason: 'cancelled' })
    await expect(biometricGate('approve', 'approve x')).resolves.toBe(false)
    expect(settingsGet).toHaveBeenCalledTimes(1)
    expect(authenticate).toHaveBeenCalledTimes(1)
  })
})

describe('requireBiometric', () => {
  it('throws TouchIdDeclined when the gate says no, and nothing when it says yes', async () => {
    setMode('all')
    authenticate.mockResolvedValueOnce({ ok: false, reason: 'cancelled' })
    const err = await requireBiometric('vault', 'remove x').catch((e: unknown) => e)
    expect(err).toBeInstanceOf(TouchIdDeclined)
    expect(isTouchIdDeclined(err)).toBe(true)
    expect(isTouchIdDeclined(new Error('nope'))).toBe(false)
    authenticate.mockResolvedValueOnce({ ok: true })
    await expect(requireBiometric('vault', 'remove x')).resolves.toBeUndefined()
  })
})
