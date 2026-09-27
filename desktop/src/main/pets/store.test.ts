// @vitest-environment node
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('electron', () => ({ net: { fetch: () => Promise.reject(new Error('offline')) } }))

const { PetStore, bundledPetsDir } = await import('./store')

/** A lossless-WebP header with nothing behind it: enough to read a size. */
function webpHeader(width: number, height: number): Buffer {
  const buf = Buffer.alloc(30)
  buf.write('RIFF', 0, 'ascii')
  buf.writeUInt32LE(22, 4)
  buf.write('WEBPVP8L', 8, 'ascii')
  buf.writeUInt32LE(10, 16)
  buf.writeUInt8(0x2f, 20)
  buf.writeUInt32LE((width - 1) | ((height - 1) << 14), 21)
  return buf
}

const SHEET = webpHeader(8 * 192, 9 * 208)

let dir: string
let root: string
let bundled: string

function writePet(parent: string, slug: string, displayName = slug): string {
  const petDir = path.join(parent, slug)
  mkdirSync(petDir, { recursive: true })
  writeFileSync(
    path.join(petDir, 'pet.json'),
    JSON.stringify({ id: slug, displayName, spritesheetPath: 'spritesheet.webp' }),
    'utf8',
  )
  writeFileSync(path.join(petDir, 'spritesheet.webp'), SHEET)
  return petDir
}

beforeEach(() => {
  dir = mkdtempSync(path.join(tmpdir(), 'agentos-pets-'))
  root = path.join(dir, 'pets')
  bundled = path.join(dir, 'bundled')
  mkdirSync(bundled, { recursive: true })
})

afterEach(() => rmSync(dir, { recursive: true, force: true }))

describe('PetStore.seedBundled', () => {
  it('installs a bundled pet on first launch', async () => {
    writePet(bundled, 'agentos', 'AgentOS')
    const store = new PetStore(root)

    expect(await store.seedBundled(bundled)).toEqual(['agentos'])
    const installed = await store.installed()
    expect(installed).toEqual([
      expect.objectContaining({ slug: 'agentos', displayName: 'AgentOS' }),
    ])
  })

  it('leaves a bundled pet the user removed removed', async () => {
    writePet(bundled, 'agentos')
    const store = new PetStore(root)
    await store.seedBundled(bundled)
    await store.remove('agentos')

    expect(await store.seedBundled(bundled)).toEqual([])
    expect(await store.installed()).toEqual([])
  })

  it('does not overwrite a pet already on disk under the same slug', async () => {
    writePet(bundled, 'agentos', 'AgentOS')
    writePet(root, 'agentos', 'Mine')
    const store = new PetStore(root)

    await store.seedBundled(bundled)
    expect(await store.installed()).toEqual([expect.objectContaining({ displayName: 'Mine' })])
  })

  it('adopts the pets this repo actually ships', async () => {
    const shipped = path.resolve(process.cwd(), 'resources/pets')
    const store = new PetStore(root)

    expect(await store.seedBundled(shipped)).toContain('agentos')
    expect(await store.installed()).toContainEqual(
      expect.objectContaining({ slug: 'agentos', displayName: 'AgentOS' }),
    )
  })

  it('skips a bundled folder that is not a pet, and survives no bundle at all', async () => {
    mkdirSync(path.join(bundled, 'broken'), { recursive: true })
    const store = new PetStore(root)

    expect(await store.seedBundled(bundled)).toEqual([])
    expect(await store.seedBundled(path.join(dir, 'nope'))).toEqual([])
  })
})

describe('bundledPetsDir', () => {
  it('prefers the packaged resources copy, then the repo copy', () => {
    mkdirSync(path.join(dir, 'resources', 'pets'), { recursive: true })
    expect(bundledPetsDir(path.join(dir, 'missing'), dir)).toBe(path.join(dir, 'resources', 'pets'))

    mkdirSync(path.join(dir, 'Resources', 'pets'), { recursive: true })
    expect(bundledPetsDir(path.join(dir, 'Resources'), dir)).toBe(
      path.join(dir, 'Resources', 'pets'),
    )
  })

  it('returns null when neither exists', () => {
    expect(bundledPetsDir(path.join(dir, 'a'), path.join(dir, 'b'))).toBeNull()
  })
})
