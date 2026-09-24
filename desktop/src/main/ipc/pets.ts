import { BrowserWindow, dialog, ipcMain } from 'electron'
import { IPC } from '@shared/ipc'
import type { InstalledPet } from '@shared/pet'
import type { PetStore } from '../pets/store'

export function registerPetsIpc(pets: PetStore): void {
  ipcMain.handle(IPC.pets.manifest, () => pets.fetchManifest())
  ipcMain.handle(IPC.pets.installed, () => pets.installed())
  ipcMain.handle(IPC.pets.install, (_e, slug: string) => pets.install(String(slug)))
  ipcMain.handle(IPC.pets.remove, (_e, slug: string) => pets.remove(String(slug)))
  ipcMain.handle(IPC.pets.preview, (_e, slug: string) => pets.preview(String(slug)))
  ipcMain.handle(IPC.pets.importFolder, async (event): Promise<InstalledPet | null> => {
    // The folder is the user's pick, never a path the renderer names: the
    // renderer is untrusted input and must not be able to copy from anywhere.
    const win = BrowserWindow.fromWebContents(event.sender)
    const options = {
      title: 'Choose a pet folder (pet.json + spritesheet.webp)',
      properties: ['openDirectory' as const],
    }
    const result = win
      ? await dialog.showOpenDialog(win, options)
      : await dialog.showOpenDialog(options)
    const dir = result.canceled ? null : (result.filePaths[0] ?? null)
    if (!dir) return null
    return pets.importFolder(dir)
  })
}
