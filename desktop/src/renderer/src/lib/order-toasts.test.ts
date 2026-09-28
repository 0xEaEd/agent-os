import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const toastFn = {
  success: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
  error: vi.fn(),
  dismiss: vi.fn(),
}
vi.mock('sonner', () => ({ toast: toastFn }))

const {
  SENDING_TOAST_MAX_MS,
  orderToastFinished,
  toastOrder,
  toastOrderRejected,
  toastOrderSending,
} = await import('./order-toasts')

beforeEach(() => {
  vi.useFakeTimers()
  Object.values(toastFn).forEach((fn) => fn.mockClear())
})

afterEach(() => {
  // Leave nothing tracked for the next test.
  vi.runAllTimers()
  vi.useRealTimers()
})

describe('order toasts', () => {
  it('"approved. Sending…" goes when that order finishes, not before and not for another', () => {
    toastOrderSending('lpo_a1', 'Add liquidity approved. Sending… · #1')
    expect(toastFn.success).toHaveBeenCalledWith('Add liquidity approved. Sending… · #1', {
      id: 'trd-order-lpo_a1',
    })
    orderToastFinished('lpo_other')
    expect(toastFn.dismiss).not.toHaveBeenCalled()
    orderToastFinished('lpo_a1')
    expect(toastFn.dismiss).toHaveBeenCalledWith('trd-order-lpo_a1')
    // Once: a second finished (a replay) is nothing, and the cap does not fire again.
    orderToastFinished('lpo_a1')
    vi.advanceTimersByTime(SENDING_TOAST_MAX_MS)
    expect(toastFn.dismiss).toHaveBeenCalledTimes(1)
  })

  it('never outlives its cap, even when no finished event arrives', () => {
    toastOrderSending('o1', 'Swap approved. Re-quoting and sending…')
    vi.advanceTimersByTime(SENDING_TOAST_MAX_MS - 1)
    expect(toastFn.dismiss).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1)
    expect(toastFn.dismiss).toHaveBeenCalledWith('trd-order-o1')
  })

  it('a rejection wears the warning icon, not the check mark, and finished leaves it up', () => {
    toastOrderRejected('lpo_c1', 'Collect fees Rejected · #3095089')
    expect(toastFn.warning).toHaveBeenCalledWith('Collect fees Rejected · #3095089', {
      id: 'trd-order-lpo_c1',
    })
    expect(toastFn.success).not.toHaveBeenCalled()
    // The engine announces the rejected order as finished: the toast that says so stays.
    orderToastFinished('lpo_c1')
    vi.advanceTimersByTime(SENDING_TOAST_MAX_MS)
    expect(toastFn.dismiss).not.toHaveBeenCalled()
  })

  it('a later toast on the same order replaces the sending one and drops its cap', () => {
    toastOrderSending('o2', 'Send approved. Sending…')
    toastOrder('error', 'o2', 'Could not decide: boom')
    expect(toastFn.error).toHaveBeenCalledWith('Could not decide: boom', { id: 'trd-order-o2' })
    orderToastFinished('o2')
    vi.advanceTimersByTime(SENDING_TOAST_MAX_MS)
    expect(toastFn.dismiss).not.toHaveBeenCalled()
  })
})
