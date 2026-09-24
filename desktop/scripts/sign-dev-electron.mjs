/**
 * Make the development Electron.app a real app in macOS's eyes.
 *
 * Two things stand between `npm run dev` and working notifications:
 *
 * 1. The npm-distributed `Electron.app` carries only a linker signature and
 *    no resource seal, so `codesign --verify` rejects it. macOS 15+ then
 *    refuses everything that needs an app identity, notifications first among
 *    them: `usernotificationsd` logs "addRequest not allowed" and
 *    `Notification.show()` silently does nothing. A fresh ad-hoc signature
 *    fixes the seal, and registering the bundle with LaunchServices lets the
 *    notification daemon find its record (otherwise the permission request
 *    fails with LS error -10814).
 *
 * 2. Every Electron.app on the machine ships as `com.github.Electron`. macOS
 *    attributes a notification to a bundle identifier, not a process, so a
 *    click resolves that identifier through LaunchServices and may land on
 *    any other copy (an `npx electron` cache, another project's
 *    node_modules), which opens Electron's stock welcome window instead of
 *    this app. Giving the dev bundle its own identifier and name makes the
 *    click come back here, and labels the banner "AgentOS Dev" rather than
 *    "Electron". The identifier differs from the packaged app's
 *    (`dev.agentos.desktop`) so an installed AgentOS.app and a dev run never
 *    shadow each other either.
 *
 * Runs after `npm ci` and before `npm run dev`; a no-op when the bundle is
 * already prepared, and never fails the install.
 */
import { execFileSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const LSREGISTER =
  '/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister'
const PLISTBUDDY = '/usr/libexec/PlistBuddy'

export const DEV_BUNDLE_ID = 'dev.agentos.desktop.dev'
export const DEV_BUNDLE_NAME = 'AgentOS Dev'

function run(cmd, args) {
  return execFileSync(cmd, args, { stdio: ['ignore', 'pipe', 'pipe'] })
    .toString()
    .trim()
}

function plistGet(plist, key) {
  try {
    return run(PLISTBUDDY, ['-c', `Print :${key}`, plist])
  } catch {
    return ''
  }
}

function plistSet(plist, key, value) {
  try {
    run(PLISTBUDDY, ['-c', `Set :${key} ${value}`, plist])
  } catch {
    run(PLISTBUDDY, ['-c', `Add :${key} string ${value}`, plist])
  }
}

/** Returns true when Info.plist changed (which also breaks the seal). */
function identify(app) {
  const plist = path.join(app, 'Contents', 'Info.plist')
  if (!existsSync(plist) || !existsSync(PLISTBUDDY)) return false
  const wanted = {
    CFBundleIdentifier: DEV_BUNDLE_ID,
    CFBundleName: DEV_BUNDLE_NAME,
    CFBundleDisplayName: DEV_BUNDLE_NAME,
  }
  let changed = false
  for (const [key, value] of Object.entries(wanted)) {
    if (plistGet(plist, key) === value) continue
    plistSet(plist, key, value)
    changed = true
  }
  return changed
}

function signatureValid(app) {
  try {
    run('codesign', ['--verify', '--deep', '--strict', app])
    return true
  } catch {
    return false
  }
}

function main() {
  if (process.platform !== 'darwin') return
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
  const app = path.join(root, 'node_modules', 'electron', 'dist', 'Electron.app')
  if (!existsSync(app)) return // binary not downloaded yet (sandboxed install)

  const renamed = identify(app)
  if (renamed || !signatureValid(app)) {
    // Info.plist is part of the seal, so any identity change needs a re-sign.
    run('codesign', ['--force', '--deep', '--sign', '-', app])
    console.log(
      `[desktop] prepared node_modules/electron Electron.app as ${DEV_BUNDLE_ID} (ad-hoc signed) so macOS notifications work in dev`,
    )
  }
  if (existsSync(LSREGISTER)) run(LSREGISTER, ['-f', app])
}

try {
  main()
} catch (err) {
  console.warn(
    `[desktop] could not prepare Electron.app for macOS notifications: ${err?.message ?? err}`,
  )
}
