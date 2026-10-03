/** Facts about the running shell that only the main process knows. */
export interface AppInfo {
  version: string
  electron: string
  chrome: string
  node: string
  /** `darwin`; kept for the About pane so it never hardcodes a platform. */
  platform: string
  arch: string
  /** Whether this is a packaged .app or `electron-vite dev`. */
  packaged: boolean
  paths: {
    /** ~/Library/Application Support/AgentOS */
    userData: string
    /** The JSON file settings persist to. */
    settings: string
    /** ~/Library/Logs/AgentOS */
    logs: string
  }
}

/** Filter for the native open-file sheet. */
export interface ChooseFileOptions {
  title?: string
  /** Start browsing here when it exists. */
  defaultPath?: string
  /** `file` (default) or `directory`. */
  kind?: 'file' | 'directory'
}

/** What `app.biometrics()` reports: whether Touch ID can be asked for right now. */
export interface BiometricsInfo {
  /** False on a Mac without a sensor, with none enrolled, or with the lid closed. */
  available: boolean
}

/** Why a Touch ID prompt did not confirm the user. */
export type AuthFailure = 'unavailable' | 'cancelled' | 'failed'

/** The answer of `app.authenticate(reason)`. */
export type AuthResult = { ok: true } | { ok: false; reason: AuthFailure }

/** The most a reason may hold: macOS prints it in the Touch ID sheet. */
export const AUTH_REASON_MAX = 120
