import type { QuickAskStatus } from '@shared/quick-ask'
import type { QuickAskSettings } from '@shared/settings'

/** The slice of Electron's `globalShortcut` this module needs (a fake in tests). */
export interface ShortcutRegistry {
  register(accelerator: string, callback: () => void): boolean
  unregister(accelerator: string): void
}

/**
 * Keeps the global shortcut in step with Settings › Behaviour › Quick Ask.
 * `apply` is called at boot and on every settings write: it registers the
 * chosen key, swaps it when the choice changes, releases it when Quick Ask is
 * turned off, and records a refusal (another app holds the key) as
 * `unavailable` so the pane can say so instead of failing silently.
 */
export class QuickAskHotkey {
  private registered: string | null = null
  private current: QuickAskStatus

  constructor(
    private readonly registry: ShortcutRegistry,
    private readonly onPress: () => void,
    private readonly onStatus: (status: QuickAskStatus) => void = () => {},
  ) {
    this.current = { state: 'off', shortcut: 'Alt+Space' }
  }

  status(): QuickAskStatus {
    return { ...this.current }
  }

  apply(settings: QuickAskSettings): QuickAskStatus {
    // A write to another section: the key we hold is still the one wanted.
    // A key that was refused is retried, since the app holding it may have let go.
    if (settings.enabled && this.registered === settings.shortcut) return this.status()
    this.release()
    let next: QuickAskStatus
    if (!settings.enabled) {
      next = { state: 'off', shortcut: settings.shortcut }
    } else {
      let ok = false
      try {
        ok = this.registry.register(settings.shortcut, this.onPress)
      } catch {
        ok = false
      }
      if (ok) this.registered = settings.shortcut
      next = { state: ok ? 'ready' : 'unavailable', shortcut: settings.shortcut }
    }
    const changed = next.state !== this.current.state || next.shortcut !== this.current.shortcut
    this.current = next
    if (changed) this.onStatus(this.status())
    return this.status()
  }

  /** Let go of the key (quit, or Quick Ask turned off). */
  release(): void {
    if (this.registered === null) return
    try {
      this.registry.unregister(this.registered)
    } catch {
      /* already gone */
    }
    this.registered = null
  }
}
