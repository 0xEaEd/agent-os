import { app, BrowserWindow, Menu, nativeImage, Tray, type WebContents } from 'electron'
import { IPC } from '@shared/ipc'
import type { NotifyTarget } from '@shared/notify'
import type { DesktopSettings } from '@shared/settings'
import { EMPTY_TRAY_SUMMARY, type TraySummary } from '@shared/tray'
import type { GatewaySupervisor } from '../gateway/supervisor'
import { requestOpenSettings } from '../ipc/app'
import type { SettingsStore } from '../settings/store'
import { findMainWindow } from '../window'
import { buildTrayMenu, trayTitle, type TrayActions } from './menu'
import { sanitizeTraySummary } from './summary'

/** While a DCA countdown is on the menu, redraw it this often. */
const COUNTDOWN_REFRESH_MS = 30_000

export interface MenuBarDeps {
  settings: SettingsStore
  gateway: GatewaySupervisor
  /** `trayTemplate.png`; its `@2x` twin beside it is picked up by nativeImage. */
  iconPath: string
  /** Open the main window when none exists (what `activate` does). */
  createWindow: () => BrowserWindow
}

/**
 * The status item in the macOS menu bar. It lives as long as the setting
 * says (`sync`), redraws on every input — the renderer's summary, the
 * supervisor's status, a settings write — and outlives the window: the
 * gateway keeps running after the last window closes, and this is where
 * that shows.
 *
 * Main never talks to the gateway, so what the gateway is doing (approvals,
 * live turns, the next DCA buy) is whatever the window last pushed. With no
 * window there is nobody to ask, and the menu says only what main knows.
 */
export class MenuBar {
  private tray: Tray | null = null
  private summary: TraySummary = { ...EMPTY_TRAY_SUMMARY }
  /** WebContents that have pushed a summary since their last load: their router is bound. */
  private readonly ready = new Set<number>()
  private readonly watched = new WeakSet<WebContents>()
  /** Something to hand the renderer once a window is ready for it (just created, reloading). */
  private pending: ((contents: WebContents) => void) | null = null
  private countdown: ReturnType<typeof setInterval> | null = null

  private readonly actions: TrayActions = {
    openApp: () => void this.showWindow(),
    navigate: (target) => this.deliver((contents) => sendTarget(contents, target)),
    openSettings: () => this.deliver(() => requestOpenSettings()),
    startGateway: () => void this.deps.gateway.start().catch(() => {}),
    stopGateway: () => void this.deps.gateway.stop().catch(() => {}),
    restartGateway: () => void this.deps.gateway.restart().catch(() => {}),
    quit: () => app.quit(),
  }

  constructor(private readonly deps: MenuBarDeps) {
    deps.gateway.subscribe(() => this.rebuild())
  }

  /** Apply the setting: create or remove the item, and redraw what is there. */
  sync(settings: DesktopSettings): void {
    if (settings.general.showInMenuBar) this.create()
    else this.destroy()
    this.rebuild()
  }

  /** A `tray:summary` from a window. Also marks that window ready for navigation. */
  setSummary(raw: unknown, sender: WebContents): void {
    this.summary = sanitizeTraySummary(raw)
    this.watch(sender)
    this.ready.add(sender.id)
    const pending = this.pending
    if (pending) {
      this.pending = null
      pending(sender)
    }
    this.rebuild()
  }

  private create(): void {
    if (this.tray) return
    const image = nativeImage.createFromPath(this.deps.iconPath)
    // The `Template` suffix already marks it; say so anyway for a renamed file.
    image.setTemplateImage(true)
    if (image.isEmpty()) console.warn(`[tray] icon not found at ${this.deps.iconPath}`)
    this.tray = new Tray(image)
    this.tray.setToolTip('AgentOS')
    console.info('[tray] menu bar item created')
  }

  private destroy(): void {
    if (!this.tray) return
    this.tray.destroy()
    this.tray = null
    this.stopCountdown()
    console.info('[tray] menu bar item removed')
  }

  private rebuild(): void {
    const tray = this.tray
    if (!tray || tray.isDestroyed()) return
    const template = buildTrayMenu(
      this.summary,
      this.deps.gateway.current(),
      this.deps.settings.get(),
      this.actions,
      Date.now(),
    )
    tray.setContextMenu(Menu.buildFromTemplate(template))
    tray.setTitle(trayTitle(this.summary), { fontType: 'monospacedDigit' })
    if (this.summary.nextMandate) this.startCountdown()
    else this.stopCountdown()
  }

  private startCountdown(): void {
    if (this.countdown) return
    this.countdown = setInterval(() => this.rebuild(), COUNTDOWN_REFRESH_MS)
    this.countdown.unref?.()
  }

  private stopCountdown(): void {
    if (!this.countdown) return
    clearInterval(this.countdown)
    this.countdown = null
  }

  /**
   * A window's renderer is ready from its first summary until it navigates
   * away (a reload) or is destroyed. When the last one goes, so does what it
   * reported: a closed window cannot keep "2 approvals waiting" true.
   */
  private watch(contents: WebContents): void {
    if (this.watched.has(contents)) return
    this.watched.add(contents)
    const id = contents.id
    // Only a new document (a reload) unbinds the router. Route changes are
    // same-document, and a subframe loading (a chart, an embed) is not the app.
    contents.on('did-start-navigation', (details) => {
      if (details.isMainFrame && !details.isSameDocument) this.ready.delete(id)
    })
    contents.once('destroyed', () => {
      this.ready.delete(id)
      if (this.ready.size === 0) {
        this.summary = { ...EMPTY_TRAY_SUMMARY }
        this.rebuild()
      }
    })
  }

  /** Focus, restore or create the main window, and bring the app in front. */
  private showWindow(): BrowserWindow {
    // The main window, never the Quick Ask panel (a hidden panel is still a window).
    const open = findMainWindow()
    const win = open ?? this.deps.createWindow()
    if (open) {
      if (open.isMinimized()) open.restore()
      open.show()
      open.focus()
    }
    // A menu bar click comes from whatever app is in front; take focus from it.
    app.focus({ steal: true })
    return win
  }

  /**
   * Run `send` against the window's renderer once it can act on it: now, if
   * it has reported in since its last load; otherwise when its first summary
   * arrives (a window just created, or one mid-reload). The latest request
   * wins: two clicks before the window is up open the second.
   */
  private deliver(send: (contents: WebContents) => void): void {
    const win = this.showWindow()
    if (this.ready.has(win.webContents.id)) send(win.webContents)
    else this.pending = send
  }
}

function sendTarget(contents: WebContents, target: NotifyTarget): void {
  if (!contents.isDestroyed()) contents.send(IPC.tray.navigate, target)
}
