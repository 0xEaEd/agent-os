#!/usr/bin/env node
/**
 * Prints the version electron-builder must package under.
 *
 * `package.json` holds the CalVer the whole repository releases as
 * (`2026.9.22.post1`), which is not semver; electron-builder and
 * electron-updater need one. This prints its semver twin (see
 * `calverToSemver` in src/shared/updates.ts, the same mapping kept in sync by
 * the test next to this file), for
 *
 *   npx electron-builder ... \
 *     -c.extraMetadata.version="$(node scripts/release-version.mjs)" \
 *     -c.extraMetadata.calver="$(node scripts/release-version.mjs --calver)"
 *
 * `--calver` prints the CalVer itself, so both flags come from one place.
 */
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

export function calverToSemver(calver) {
  const match = /^v?(\d{4})\.(\d{1,2})\.(\d{1,2})(?:\.post(\d+))?$/.exec(calver.trim())
  if (!match) return null
  const [, year, month, day, post] = match
  return `${Number(year)}.${Number(month) * 100 + Number(day)}.${Number(post ?? 0)}`
}

export function packageCalver() {
  const here = path.dirname(fileURLToPath(import.meta.url))
  const pkg = JSON.parse(readFileSync(path.join(here, '..', 'package.json'), 'utf8'))
  return pkg.version
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const calver = packageCalver()
  if (process.argv.includes('--calver')) {
    process.stdout.write(`${calver}\n`)
  } else {
    const semver = calverToSemver(calver)
    if (!semver) {
      process.stderr.write(`desktop/package.json version is not a CalVer: ${calver}\n`)
      process.exit(1)
    }
    process.stdout.write(`${semver}\n`)
  }
}
