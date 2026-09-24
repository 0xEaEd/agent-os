import {
  idleAppState,
  semverToCalver,
  type AppInstallBlock,
  type AppUpdateState,
} from '@shared/updates'

type Listener = (state: AppUpdateState) => void

/**
 * The slice of `electron-updater`'s `AppUpdater` this controller drives.
 * Declared here so the controller (and its tests) never import the package,
 * which insists on a real Electron `app` at load time.
 */
export interface UpdaterLike {
  autoDownload: boolean
  autoInstallOnAppQuit: boolean
  allowPrerelease: boolean
  allowDowngrade?: boolean
  on(event: 'checking-for-update', listener: () => void): unknown
  on(event: 'update-available', listener: (info: { version: string }) => void): unknown
  on(event: 'update-not-available', listener: (info: { version: string }) => void): unknown
  on(event: 'download-progress', listener: (progress: { percent: number }) => void): unknown
  on(event: 'update-downloaded', listener: (info: { version: string }) => void): unknown
  on(event: 'error', listener: (error: Error) => void): unknown
  checkForUpdates(): Promise<unknown>
  downloadUpdate(): Promise<unknown>
  quitAndInstall(isSilent?: boolean, isForceRunAfter?: boolean): void
}

/** What a restart would interrupt right now, or `null` when nothing. */
export type InstallGate = () => AppInstallBlock | null

export interface AppUpdateControllerDeps {
  /** `null` when there is nothing to update from (dev build, no channel). */
  updater: UpdaterLike | null
  version: string
  /** Runs before `quitAndInstall`: stop the managed gateway, mark the quit. */
  beforeInstall: () => Promise<void>
  /**
   * Consulted before every restart. A non-null reason (an engine install in
   * progress, the first-run installer running) blocks the relaunch and is
   * shown to the user; nothing is interrupted behind their back.
   */
  installGate?: InstallGate
  now?: () => number
}

export interface CheckOptions {
  /**
   * An ambient check (launch, focus, the periodic tick) rather than a click.
   * It never surfaces an error and never rewinds a state the user is acting
   * on: an in-flight download, a downloaded build waiting for its restart, or
   * an error banner they have already seen.
   */
  silent?: boolean
}

/**
 * State machine over `electron-updater` for the About pane and the shell's
 * update toast. Two explicit steps, the way Vex does it: nothing downloads
 * without a click and nothing installs without a second one. The swap
 * happens on the relaunch that click triggers; a plain quit leaves the
 * running build alone (`autoInstallOnAppQuit` is off), so closing the app
 * mid-session never turns into a surprise upgrade.
 */
export class AppUpdateController {
  private state: AppUpdateState
  private readonly listeners = new Set<Listener>()
  private readonly now: () => number
  private silentCheck = false
  private resumePhase: 'idle' | 'up-to-date' = 'idle'

  constructor(private readonly deps: AppUpdateControllerDeps) {
    this.now = deps.now ?? Date.now
    this.state = idleAppState(deps.version)
    if (!deps.updater) {
      this.state = { ...this.state, phase: 'unsupported' }
      return
    }
    const u = deps.updater
    u.autoDownload = false
    u.autoInstallOnAppQuit = false
    u.allowPrerelease = false
    u.allowDowngrade = false
    u.on('checking-for-update', () => {
      // A silent check only announces itself from a quiet state; flashing
      // "checking" over an available/error banner would dismiss it for nothing.
      if (this.silentCheck && !this.quiet()) return
      this.set({ ...this.state, phase: 'checking', error: null })
    })
    u.on('update-available', (info) => {
      if (this.silentResultMustYield()) return
      this.set({
        ...this.state,
        phase: 'available',
        latest: semverToCalver(info.version),
        percent: null,
        checkedAt: this.now(),
        error: null,
      })
    })
    u.on('update-not-available', (info) => {
      if (this.silentResultMustYield()) return
      this.set({
        ...this.state,
        phase: 'up-to-date',
        latest: semverToCalver(info.version),
        checkedAt: this.now(),
        error: null,
      })
    })
    u.on('download-progress', (progress) =>
      this.set({
        ...this.state,
        phase: 'downloading',
        percent: Math.max(0, Math.min(100, Math.round(progress.percent))),
      }),
    )
    u.on('update-downloaded', (info) =>
      this.set({
        ...this.state,
        phase: 'downloaded',
        latest: semverToCalver(info.version),
        percent: 100,
      }),
    )
    u.on('error', (error) => {
      // Ambient failures (offline, a rate-limited GitHub API) stay quiet; the
      // next tick retries. Only a click's failure earns a banner.
      if (this.silentCheck) return this.settleSilent()
      this.set({ ...this.state, phase: 'error', percent: null, error: error.message })
    })
  }

  current(): AppUpdateState {
    return { ...this.state }
  }

  subscribe(fn: Listener): () => void {
    this.listeners.add(fn)
    return () => this.listeners.delete(fn)
  }

  async check(options: CheckOptions = {}): Promise<AppUpdateState> {
    const u = this.deps.updater
    if (!u || this.state.phase === 'checking' || this.state.phase === 'downloading') {
      return this.current()
    }
    // A downloaded update stays downloaded; re-checking would only re-offer it.
    if (this.state.phase === 'downloaded') return this.current()
    const silent = options.silent === true
    // A silent check has nothing to add over an error the user already sees;
    // only a click retries from there.
    if (silent && this.state.phase === 'error') return this.current()
    this.silentCheck = silent
    this.resumePhase = this.state.phase === 'up-to-date' ? 'up-to-date' : 'idle'
    try {
      await u.checkForUpdates()
    } catch (err) {
      if (silent) this.settleSilent()
      else this.set({ ...this.state, phase: 'error', error: errorMessage(err) })
    } finally {
      this.silentCheck = false
    }
    return this.current()
  }

  /** A silent check that failed leaves no "Checking…" behind. */
  private settleSilent(): void {
    if (this.state.phase === 'checking') this.set({ ...this.state, phase: this.resumePhase })
  }

  async download(): Promise<AppUpdateState> {
    const u = this.deps.updater
    if (!u || this.state.phase !== 'available') return this.current()
    this.set({ ...this.state, phase: 'downloading', percent: 0, error: null, blocked: null })
    try {
      await u.downloadUpdate()
    } catch (err) {
      this.set({ ...this.state, phase: 'error', percent: null, error: errorMessage(err) })
    }
    return this.current()
  }

  async install(): Promise<AppUpdateState> {
    const u = this.deps.updater
    if (!u || this.state.phase !== 'downloaded') return this.current()
    const blocked = this.deps.installGate?.() ?? null
    if (blocked) return this.set({ ...this.state, blocked })
    if (this.state.blocked) this.set({ ...this.state, blocked: null })
    await this.deps.beforeInstall()
    // Not silent (Squirrel shows its own progress) and relaunch afterwards.
    u.quitAndInstall(false, true)
    return this.current()
  }

  /** Nothing on screen a silent check could disturb. */
  private quiet(): boolean {
    return this.state.phase === 'idle' || this.state.phase === 'up-to-date'
  }

  /**
   * A silent check's result arrives after an HTTP round-trip; a download the
   * user started meanwhile must not be rewound to `available` / `up-to-date`.
   */
  private silentResultMustYield(): boolean {
    if (!this.silentCheck) return false
    return this.state.phase === 'downloading' || this.state.phase === 'downloaded'
  }

  private set(next: AppUpdateState): AppUpdateState {
    this.state = next
    for (const fn of this.listeners) fn({ ...next })
    return { ...next }
  }
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}
