import { IPC } from '@shared/ipc'
import {
  parseQuickAskSubmission,
  type QuickAskStatus,
  type QuickAskSubmission,
} from '@shared/quick-ask'
import type { QuickAskSettings } from '@shared/settings'
import { QuickAskHotkey, type ShortcutRegistry } from './hotkey'
import { QuickAskInbox } from './inbox'

/** What the controller needs from the panel window (QuickAskPanel; a fake in tests). */
export interface PanelLike {
  ensure(): unknown
  toggle(): void
  show(): void
  hide(): void
  destroy(): void
  owns(sender: unknown): boolean
  markReady(): void
  resize(raw: unknown): void
}

/** What the controller needs from the main window. */
export interface MainWindowLike {
  isMinimized(): boolean
  restore(): void
  show(): void
  focus(): void
  webContents: { isLoading(): boolean; send(channel: string): void }
}

export interface QuickAskDeps {
  registry: ShortcutRegistry
  panel: PanelLike
  findMainWindow(): MainWindowLike | null
  /** Create the main window (the app is alive in the Dock with none open). */
  openMainWindow(): void
  /** Make AgentOS the active app, so the window it brings forward has the keyboard. */
  focusApp(): void
  /** Tell every renderer something changed. */
  broadcast(channel: string, payload: unknown): void
}

/**
 * Quick Ask, main side: the hotkey, the panel and the hand-off to the main
 * window. The text travels over IPC only: the panel submits, main validates,
 * hides the panel, brings the main window forward and pings it; the main
 * window collects from the inbox and sends through its own gateway
 * connection. The panel never talks to the gateway.
 */
export class QuickAskController {
  readonly inbox = new QuickAskInbox()
  private readonly hotkey: QuickAskHotkey

  constructor(private readonly deps: QuickAskDeps) {
    this.hotkey = new QuickAskHotkey(
      deps.registry,
      () => deps.panel.toggle(),
      (status) => deps.broadcast(IPC.quickAsk.statusChanged, status),
    )
  }

  /** At boot and whenever the Quick Ask settings change. */
  apply(settings: QuickAskSettings): QuickAskStatus {
    const status = this.hotkey.apply(settings)
    // Created once and kept hidden while Quick Ask is on, so a press is
    // instant; no window kept around for a key that is not registered.
    if (status.state === 'ready') this.deps.panel.ensure()
    else this.deps.panel.destroy()
    return status
  }

  status(): QuickAskStatus {
    return this.hotkey.status()
  }

  /**
   * Open the panel from somewhere other than the key (the menu bar). Only
   * while Quick Ask is on; the panel is then the one `apply` keeps.
   */
  show(): void {
    if (this.hotkey.status().state === 'off') return
    this.deps.panel.show()
  }

  /**
   * The panel pressed Return. Refused (false) when the sender is not the
   * panel or the payload does not validate; the panel then stays open with
   * its text.
   */
  submit(sender: unknown, raw: unknown): boolean {
    if (!this.deps.panel.owns(sender)) return false
    const submission = parseQuickAskSubmission(raw)
    if (!submission) return false
    this.deps.panel.hide()
    this.deliver(submission)
    return true
  }

  /** Escape in the panel. */
  hide(sender: unknown): void {
    if (this.deps.panel.owns(sender)) this.deps.panel.hide()
  }

  ready(sender: unknown): void {
    if (this.deps.panel.owns(sender)) this.deps.panel.markReady()
  }

  resize(sender: unknown, height: unknown): void {
    if (this.deps.panel.owns(sender)) this.deps.panel.resize(height)
  }

  /** The main window collecting what is waiting. The panel has nothing to collect. */
  take(sender: unknown): QuickAskSubmission[] {
    if (this.deps.panel.owns(sender)) return []
    return this.inbox.take()
  }

  /** Quit: let go of the key. */
  dispose(): void {
    this.hotkey.release()
  }

  private deliver(submission: QuickAskSubmission): void {
    this.inbox.push(submission)
    const win = this.deps.findMainWindow()
    if (!win) {
      // Closed (the app lives on in the Dock): a new window collects the
      // inbox as soon as its renderer mounts.
      this.deps.openMainWindow()
      return
    }
    if (win.isMinimized()) win.restore()
    win.show()
    win.focus()
    this.deps.focusApp()
    // A window still loading collects on mount instead of on the ping.
    if (!win.webContents.isLoading()) win.webContents.send(IPC.quickAsk.deliver)
  }
}
