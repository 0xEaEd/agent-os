import { APP_UPDATE_CHECK_INTERVAL_MS } from '@shared/updates'

/** The three moments an ambient check runs, besides a click in About. */
export interface AutoCheckDeps {
  /** A silent check: no banner on failure, no rewinding an active download. */
  check(): Promise<unknown>
  /** Subscribes to "a window gained focus"; returns the unsubscribe. */
  onFocus(listener: () => void): () => void
  /** After launch, before the first check: the gateway boot gets the network first. */
  initialDelayMs?: number
  intervalMs?: number
  /** Focus checks closer together than this collapse into one. */
  focusDebounceMs?: number
  now?: () => number
  setTimeout?: typeof globalThis.setTimeout
  setInterval?: typeof globalThis.setInterval
  clearTimeout?: typeof globalThis.clearTimeout
  clearInterval?: typeof globalThis.clearInterval
}

const DEFAULT_INITIAL_DELAY_MS = 15 * 1000
const DEFAULT_FOCUS_DEBOUNCE_MS = 60 * 1000

/**
 * Looks for a new build at launch, whenever the window comes back to the
 * front and on a periodic tick, the way Vex does it. Every check is silent:
 * a hit shows up as the shell's update toast and in About, a miss or a
 * failure shows nothing. Returns the disposer.
 */
export function startAutoCheck(deps: AutoCheckDeps): () => void {
  const now = deps.now ?? Date.now
  const setT = deps.setTimeout ?? globalThis.setTimeout
  const setI = deps.setInterval ?? globalThis.setInterval
  const clearT = deps.clearTimeout ?? globalThis.clearTimeout
  const clearI = deps.clearInterval ?? globalThis.clearInterval
  const debounce = deps.focusDebounceMs ?? DEFAULT_FOCUS_DEBOUNCE_MS

  let lastAt = -Infinity
  let inFlight = false
  const run = async (): Promise<void> => {
    if (inFlight) return
    inFlight = true
    lastAt = now()
    try {
      await deps.check()
    } catch {
      /* silent by contract; the controller already swallowed what it could */
    } finally {
      inFlight = false
    }
  }

  const initial = setT(() => void run(), deps.initialDelayMs ?? DEFAULT_INITIAL_DELAY_MS)
  const tick = setI(() => void run(), deps.intervalMs ?? APP_UPDATE_CHECK_INTERVAL_MS)
  const offFocus = deps.onFocus(() => {
    if (now() - lastAt < debounce) return
    void run()
  })

  return () => {
    clearT(initial)
    clearI(tick)
    offFocus()
  }
}
