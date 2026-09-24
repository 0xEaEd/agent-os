import { app } from 'electron'
import { readFileSync } from 'node:fs'
import path from 'node:path'

let cached: string | null = null

/**
 * The CalVer this app was released as, e.g. `2026.9.22.post1`.
 *
 * A packaged app's `package.json` carries the semver twin as `version` (what
 * electron-updater compares; see `calverToSemver` in shared/updates.ts) and
 * the CalVer as `calver`, both written by electron-builder's extraMetadata
 * at release time. A dev run has only the CalVer under `version`. Everything
 * a person or the engine installer sees uses this one; only
 * `app.getVersion()` (the twin) goes to electron-updater.
 */
export function appCalver(): string {
  if (cached) return cached
  cached = readCalver() ?? app.getVersion()
  return cached
}

function readCalver(): string | null {
  try {
    const pkg = JSON.parse(readFileSync(path.join(app.getAppPath(), 'package.json'), 'utf8')) as {
      calver?: unknown
    }
    return typeof pkg.calver === 'string' && pkg.calver ? pkg.calver : null
  } catch {
    return null
  }
}
