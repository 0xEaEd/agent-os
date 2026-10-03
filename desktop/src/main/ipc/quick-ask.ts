import { ipcMain } from 'electron'
import { IPC } from '@shared/ipc'
import type { QuickAskController } from '../quick-ask/controller'

/**
 * Quick Ask's doors. The panel's own handlers (submit, hide, ready, resize)
 * only answer the panel's renderer; `take` only answers the main window.
 * Every payload is validated in the controller before it goes anywhere.
 */
export function registerQuickAskIpc(quickAsk: QuickAskController): void {
  ipcMain.handle(IPC.quickAsk.submit, (e, raw: unknown) => quickAsk.submit(e.sender, raw))
  ipcMain.handle(IPC.quickAsk.hide, (e) => quickAsk.hide(e.sender))
  ipcMain.handle(IPC.quickAsk.ready, (e) => quickAsk.ready(e.sender))
  ipcMain.handle(IPC.quickAsk.resize, (e, height: unknown) => quickAsk.resize(e.sender, height))
  ipcMain.handle(IPC.quickAsk.take, (e) => quickAsk.take(e.sender))
  ipcMain.handle(IPC.quickAsk.status, () => quickAsk.status())
}
