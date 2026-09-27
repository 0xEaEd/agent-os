// @vitest-environment node
import { EventEmitter } from 'node:events'
import { describe, expect, it, vi } from 'vitest'
import { AppUpdateController, type UpdaterLike } from './app-updater'

function fakeUpdater() {
  const emitter = new EventEmitter()
  const updater = {
    autoDownload: true,
    autoInstallOnAppQuit: true,
    allowPrerelease: true,
    on: (event: string, listener: (...args: unknown[]) => void) => emitter.on(event, listener),
    checkForUpdates: vi.fn(async () => {
      emitter.emit('checking-for-update')
    }),
    downloadUpdate: vi.fn(async () => {
      emitter.emit('download-progress', { percent: 42.6 })
      emitter.emit('update-downloaded', { version: '2026.9.12' })
    }),
    quitAndInstall: vi.fn(),
  } as unknown as UpdaterLike & {
    checkForUpdates: ReturnType<typeof vi.fn>
    downloadUpdate: ReturnType<typeof vi.fn>
    quitAndInstall: ReturnType<typeof vi.fn>
  }
  return { updater, emitter }
}

describe('AppUpdateController', () => {
  it('is unsupported without an updater (dev build)', async () => {
    const ctl = new AppUpdateController({
      updater: null,
      version: '0.1.0',
      beforeInstall: async () => {},
    })
    expect(ctl.current()).toMatchObject({ phase: 'unsupported', current: '0.1.0' })
    expect((await ctl.check()).phase).toBe('unsupported')
    expect((await ctl.download()).phase).toBe('unsupported')
  })

  it('configures two explicit steps: no auto-download, no install on quit', () => {
    const { updater } = fakeUpdater()
    new AppUpdateController({ updater, version: '1', beforeInstall: async () => {} })
    expect(updater.autoDownload).toBe(false)
    expect(updater.autoInstallOnAppQuit).toBe(false)
    expect(updater.allowPrerelease).toBe(false)
    expect(updater.allowDowngrade).toBe(false)
  })

  it('walks checking → available → downloading → downloaded, then installs', async () => {
    const { updater, emitter } = fakeUpdater()
    const beforeInstall = vi.fn(async () => {})
    const ctl = new AppUpdateController({
      updater,
      version: '2026.9.9',
      beforeInstall,
      now: () => 5,
    })
    const phases: string[] = []
    ctl.subscribe((s) => phases.push(s.phase))

    await ctl.check()
    emitter.emit('update-available', { version: '2026.9.12' })
    expect(ctl.current()).toMatchObject({ phase: 'available', latest: '2026.9.12', checkedAt: 5 })

    await ctl.download()
    expect(ctl.current()).toMatchObject({ phase: 'downloaded', latest: '2026.9.12', percent: 100 })
    expect(phases).toEqual(['checking', 'available', 'downloading', 'downloading', 'downloaded'])

    await ctl.install()
    expect(beforeInstall).toHaveBeenCalledTimes(1)
    expect(updater.quitAndInstall).toHaveBeenCalledWith(false, true)
  })

  it('shows the semver twin from the feed as the CalVer it stands for', async () => {
    const { updater, emitter } = fakeUpdater()
    const ctl = new AppUpdateController({
      updater,
      version: '2026.9.22',
      beforeInstall: async () => {},
    })
    await ctl.check()
    emitter.emit('update-available', { version: '2026.922.1' })
    expect(ctl.current()).toMatchObject({ phase: 'available', latest: '2026.9.22.post1' })
    emitter.emit('update-downloaded', { version: '2026.922.1' })
    expect(ctl.current().latest).toBe('2026.9.22.post1')
  })

  it('reports up-to-date and errors', async () => {
    const { updater, emitter } = fakeUpdater()
    const ctl = new AppUpdateController({ updater, version: '1', beforeInstall: async () => {} })
    await ctl.check()
    emitter.emit('update-not-available', { version: '1' })
    expect(ctl.current().phase).toBe('up-to-date')
    emitter.emit('error', new Error('net down'))
    expect(ctl.current()).toMatchObject({ phase: 'error', error: 'net down' })
  })

  it('turns a rejected check into an error state', async () => {
    const { updater } = fakeUpdater()
    updater.checkForUpdates.mockRejectedValue(new Error('403'))
    const ctl = new AppUpdateController({ updater, version: '1', beforeInstall: async () => {} })
    expect((await ctl.check()).error).toBe('403')
  })

  it('download and install are no-ops outside their phase', async () => {
    const { updater } = fakeUpdater()
    const ctl = new AppUpdateController({ updater, version: '1', beforeInstall: async () => {} })
    await ctl.download()
    await ctl.install()
    expect(updater.downloadUpdate).not.toHaveBeenCalled()
    expect(updater.quitAndInstall).not.toHaveBeenCalled()
  })

  describe('silent checks', () => {
    it('finds a build without ever showing an error', async () => {
      const { updater, emitter } = fakeUpdater()
      const ctl = new AppUpdateController({ updater, version: '1', beforeInstall: async () => {} })
      const phases: string[] = []
      ctl.subscribe((s) => phases.push(s.phase))

      updater.checkForUpdates.mockRejectedValueOnce(new Error('offline'))
      expect((await ctl.check({ silent: true })).phase).toBe('idle')

      // An emitted error mid-check leaves no "Checking…" behind either.
      updater.checkForUpdates.mockImplementationOnce(async () => {
        emitter.emit('checking-for-update')
        emitter.emit('error', new Error('rate limited'))
      })
      expect((await ctl.check({ silent: true })).phase).toBe('idle')

      updater.checkForUpdates.mockImplementationOnce(async () => {
        emitter.emit('update-available', { version: '2' })
      })
      expect((await ctl.check({ silent: true })).phase).toBe('available')
      expect(phases).not.toContain('error')
      expect(phases).toEqual(['checking', 'idle', 'available'])
    })

    it('does not retry over an error the user already sees', async () => {
      const { updater } = fakeUpdater()
      updater.checkForUpdates.mockRejectedValueOnce(new Error('403'))
      const ctl = new AppUpdateController({ updater, version: '1', beforeInstall: async () => {} })
      await ctl.check()
      expect(ctl.current().phase).toBe('error')
      await ctl.check({ silent: true })
      expect(updater.checkForUpdates).toHaveBeenCalledTimes(1)
      // A click retries.
      updater.checkForUpdates.mockResolvedValueOnce(undefined)
      await ctl.check()
      expect(updater.checkForUpdates).toHaveBeenCalledTimes(2)
    })

    it('does not flash "checking" over an available build, and its late result yields to a download', async () => {
      const { updater, emitter } = fakeUpdater()
      const ctl = new AppUpdateController({ updater, version: '1', beforeInstall: async () => {} })
      emitter.emit('update-available', { version: '2' })
      const phases: string[] = []
      ctl.subscribe((s) => phases.push(s.phase))

      let finish: () => void = () => {}
      updater.checkForUpdates.mockImplementationOnce(
        () =>
          new Promise<void>((resolve) => {
            emitter.emit('checking-for-update')
            finish = resolve
          }),
      )
      const silent = ctl.check({ silent: true })
      expect(ctl.current().phase).toBe('available')

      // The user clicks Download while the silent round-trip is in flight …
      updater.downloadUpdate.mockImplementationOnce(async () => {
        emitter.emit('update-downloaded', { version: '2' })
      })
      await ctl.download()
      // … and its late "not available" must not rewind the downloaded build.
      emitter.emit('update-not-available', { version: '1' })
      finish()
      await silent
      expect(ctl.current().phase).toBe('downloaded')
      expect(phases).not.toContain('checking')
    })
  })

  describe('the install gate', () => {
    it('refuses the restart while something would be interrupted, then lets it through', async () => {
      const { updater, emitter } = fakeUpdater()
      const beforeInstall = vi.fn(async () => {})
      let busy: 'engine-updating' | null = 'engine-updating'
      const ctl = new AppUpdateController({
        updater,
        version: '1',
        beforeInstall,
        installGate: () => busy,
      })
      emitter.emit('update-downloaded', { version: '2' })

      expect((await ctl.install()).blocked).toBe('engine-updating')
      expect(ctl.current().phase).toBe('downloaded')
      expect(beforeInstall).not.toHaveBeenCalled()
      expect(updater.quitAndInstall).not.toHaveBeenCalled()

      busy = null
      const seen: Array<string | null> = []
      ctl.subscribe((s) => seen.push(s.blocked))
      await ctl.install()
      expect(seen).toEqual([null])
      expect(beforeInstall).toHaveBeenCalledTimes(1)
      expect(updater.quitAndInstall).toHaveBeenCalledWith(false, true)
    })
  })
})
