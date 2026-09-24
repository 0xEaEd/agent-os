// @vitest-environment node
import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'
import { calverToSemver } from './updates'

const script = path.resolve(__dirname, '../../scripts/release-version.mjs')
const run = (...args: string[]) =>
  execFileSync('node', [script, ...args], { encoding: 'utf8' }).trim()

/**
 * scripts/release-version.mjs feeds electron-builder's extraMetadata and
 * cannot import TypeScript, so it carries its own copy of the mapping; this
 * keeps the copy honest against the one the running app uses.
 */
describe('scripts/release-version.mjs', () => {
  it('prints the semver twin of package.json and the CalVer itself', () => {
    const pkg = JSON.parse(readFileSync(path.resolve(__dirname, '../../package.json'), 'utf8')) as {
      version: string
    }
    expect(run('--calver')).toBe(pkg.version)
    expect(run()).toBe(calverToSemver(pkg.version))
  })
})
