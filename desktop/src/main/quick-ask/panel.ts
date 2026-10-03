import { BrowserWindow, screen, type WebContents } from 'electron'
import { IPC } from '@shared/ipc'
import { clampPanelHeight } from '@shared/quick-ask'
import { loadRenderer, markPanelWindow, pageBackground, rendererWebPreferences } from '../window'
import { PANEL_INITIAL_HEIGHT, PANEL_WIDTH, panelBounds, type Rect } from './geometry'

/** The hash route the panel's renderer mounts (see renderer/src/app/App.tsx). */
export const QUICK_ASK_ROUTE = '/quick-ask'

/**
 * The floating prompt. One frameless HUD window, created once and then only
 * shown and hidden, so the hotkey opens it instantly. It is a `panel`: it
 * floats over other apps (full-screen ones too) and takes the keyboard
 * without making AgentOS the active app, the way Spotlight does. It hides
 * when it loses focus, and it is never shown before its view has mounted.
 */
export class QuickAskPanel {
  private win: BrowserWindow | null = null
  private ready = false
  private showWhenReady = false
  private contentHeight = PANEL_INITIAL_HEIGHT
  private workArea: Rect | null = null

  constructor(private readonly opts: { reduceTransparency: () => boolean }) {}

  // Reduce transparency reaches this window through window.ts applyVibrancy,
  // which gives a panel the `hud` material and every window its ground.

  /** Create the window if there is none. Called at boot so the first press is instant. */
  ensure(): BrowserWindow {
    if (this.win && !this.win.isDestroyed()) return this.win
    const reduceTransparency = this.opts.reduceTransparency()
    const win = new BrowserWindow({
      width: PANEL_WIDTH,
      height: PANEL_INITIAL_HEIGHT,
      show: false,
      frame: false,
      type: 'panel',
      alwaysOnTop: true,
      resizable: false,
      movable: false,
      minimizable: false,
      maximizable: false,
      fullscreenable: false,
      skipTaskbar: true,
      hasShadow: true,
      backgroundColor: pageBackground(reduceTransparency),
      vibrancy: reduceTransparency ? undefined : 'hud',
      visualEffectState: 'active',
      webPreferences: rendererWebPreferences(),
    })
    markPanelWindow(win)
    win.setAlwaysOnTop(true, 'floating')
    win.setVisibleOnAllWorkspaces(true, {
      visibleOnFullScreen: true,
      skipTransformProcessType: true,
    })
    // Clicking away closes it; nothing is sent.
    win.on('blur', () => this.hide())
    win.on('closed', () => {
      if (this.win === win) {
        this.win = null
        this.ready = false
      }
    })
    // A reload (or a crash) unmounts the view: wait for it to report again.
    win.webContents.on('did-start-loading', () => {
      this.ready = false
    })
    this.win = win
    this.ready = false
    loadRenderer(win, QUICK_ASK_ROUTE)
    return win
  }

  isVisible(): boolean {
    return Boolean(this.win && !this.win.isDestroyed() && this.win.isVisible())
  }

  /** The hotkey: open over whatever is in front, or close if already open. */
  toggle(): void {
    if (this.isVisible()) this.hide()
    else this.show()
  }

  show(): void {
    const win = this.ensure()
    if (!this.ready) {
      this.showWhenReady = true
      return
    }
    // On the display the user is looking at: the one under the cursor.
    const display = screen.getDisplayNearestPoint(screen.getCursorScreenPoint())
    this.workArea = display.workArea
    this.place(win)
    win.show()
    win.focus()
    win.webContents.send(IPC.quickAsk.shown)
  }

  hide(): void {
    this.showWhenReady = false
    if (this.win && !this.win.isDestroyed() && this.win.isVisible()) this.win.hide()
  }

  /** Quick Ask turned off: no window kept around for a key that is not registered. */
  destroy(): void {
    this.showWhenReady = false
    const win = this.win
    this.win = null
    this.ready = false
    if (win && !win.isDestroyed()) win.destroy()
  }

  /** Is this IPC sender the panel's own renderer? */
  owns(sender: WebContents): boolean {
    return Boolean(this.win && !this.win.isDestroyed() && this.win.webContents === sender)
  }

  /** The view has mounted. A press that came before it is honoured now. */
  markReady(): void {
    this.ready = true
    if (this.showWhenReady) {
      this.showWhenReady = false
      this.show()
    }
  }

  /** The field grew or shrank: follow it, keeping the top edge where it is. */
  resize(raw: unknown): void {
    const height = clampPanelHeight(raw)
    if (height === null) return
    this.contentHeight = height
    if (this.win && !this.win.isDestroyed() && this.workArea) this.place(this.win)
  }

  private place(win: BrowserWindow): void {
    if (!this.workArea) return
    win.setBounds(panelBounds(this.workArea, this.contentHeight, win.webContents.getZoomFactor()))
  }
}
