// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { startAutoCheck } from './auto-check'

describe('startAutoCheck', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  function harness(check = vi.fn(async () => {})) {
    const focusListeners = new Set<() => void>()
    const stop = startAutoCheck({
      check,
      onFocus: (listener) => {
        focusListeners.add(listener)
        return () => focusListeners.delete(listener)
      },
      initialDelayMs: 1000,
      intervalMs: 10_000,
      focusDebounceMs: 3000,
    })
    const focus = () => focusListeners.forEach((fn) => fn())
    return { check, stop, focus, focusListeners }
  }

  it('checks after the launch delay, then on every tick', async () => {
    const { check, stop } = harness()
    expect(check).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(1000)
    expect(check).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(20_000)
    expect(check).toHaveBeenCalledTimes(3)
    stop()
    await vi.advanceTimersByTimeAsync(50_000)
    expect(check).toHaveBeenCalledTimes(3)
  })

  it('checks on focus, but not twice within the debounce window', async () => {
    const { check, focus } = harness()
    focus()
    expect(check).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(500)
    focus()
    expect(check).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(3000)
    focus()
    expect(check).toHaveBeenCalledTimes(2)
  })

  it('never overlaps two checks and survives a rejected one', async () => {
    let release: () => void = () => {}
    const check = vi
      .fn<() => Promise<void>>()
      .mockImplementationOnce(() => new Promise<void>((resolve) => (release = resolve)))
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValue(undefined)
    const { focus, stop } = harness(check)
    await vi.advanceTimersByTimeAsync(1000)
    expect(check).toHaveBeenCalledTimes(1)
    // A tick while the first check is still in flight is dropped.
    await vi.advanceTimersByTimeAsync(10_000)
    expect(check).toHaveBeenCalledTimes(1)
    release()
    await vi.advanceTimersByTimeAsync(10_000)
    expect(check).toHaveBeenCalledTimes(2)
    // The rejection is swallowed; the scheduler keeps going.
    await vi.advanceTimersByTimeAsync(10_000)
    expect(check).toHaveBeenCalledTimes(3)
    stop()
    focus()
    expect(check).toHaveBeenCalledTimes(3)
  })

  it('unsubscribes from focus on stop', () => {
    const { stop, focusListeners } = harness()
    expect(focusListeners.size).toBe(1)
    stop()
    expect(focusListeners.size).toBe(0)
  })
})
