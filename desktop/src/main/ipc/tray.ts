import { ipcMain } from 'electron'
import { IPC } from '@shared/ipc'
import type { MenuBar } from '../tray/tray'

/**
 * The renderer's half of the menu bar item: a fire-and-forget summary of
 * what the gateway is doing. `MenuBar.setSummary` validates it (counts
 * clamped, the label cut to one short line) before it reaches a menu row.
 */
export function registerTrayIpc(menuBar: MenuBar): void {
  ipcMain.on(IPC.tray.summary, (event, raw: unknown) => menuBar.setSummary(raw, event.sender))
}
