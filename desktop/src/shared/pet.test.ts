import { readFileSync } from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'
import {
  derivePetState,
  isPetSlug,
  parsePetFolder,
  petStateRow,
  rowFrameCounts,
  sheetGeometry,
  webpSize,
} from './pet'

describe('webpSize', () => {
  function riff(fourcc: string, payload: number[]): Uint8Array {
    const bytes = new Uint8Array(12 + 8 + Math.max(payload.length, 12))
    bytes.set(
      [...'RIFF'].map((c) => c.charCodeAt(0)),
      0,
    )
    bytes.set(
      [...'WEBP'].map((c) => c.charCodeAt(0)),
      8,
    )
    bytes.set(
      [...fourcc].map((c) => c.charCodeAt(0)),
      12,
    )
    bytes.set(payload, 20)
    return bytes
  }

  it('reads a lossless (VP8L) header', () => {
    const bits = (1536 - 1) | ((1872 - 1) << 14)
    const payload = [0x2f, bits & 0xff, (bits >>> 8) & 0xff, (bits >>> 16) & 0xff, bits >>> 24]
    expect(webpSize(riff('VP8L', payload))).toEqual({ width: 1536, height: 1872 })
  })

  it('reads a lossy (VP8) header', () => {
    const payload = [0, 0, 0, 0x9d, 0x01, 0x2a, 0x00, 0x06, 0x50, 0x07]
    expect(webpSize(riff('VP8 ', payload))).toEqual({ width: 1536, height: 1872 })
  })

  it('reads an extended (VP8X) canvas size', () => {
    const payload = [0x10, 0, 0, 0, 0xff, 0x05, 0x00, 0x4f, 0x07, 0x00]
    expect(webpSize(riff('VP8X', payload))).toEqual({ width: 1536, height: 1872 })
  })

  it('refuses bytes that are not a WebP', () => {
    expect(webpSize(new Uint8Array(4))).toBeNull()
    expect(webpSize(riff('JUNK', [0]))).toBeNull()
    expect(webpSize(riff('VP8 ', [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]))).toBeNull()
  })

  it('sizes the sheet this repo ships, and it is a whole frame grid', () => {
    const sheet = readFileSync(
      path.resolve(process.cwd(), 'resources/pets/agentos/spritesheet.webp'),
    )
    const size = webpSize(sheet)
    expect(size).toEqual({ width: 1536, height: 1872 })
    expect(sheetGeometry(size!.width, size!.height)).toEqual({ cols: 8, rows: 9 })
  })
})

describe('parsePetFolder', () => {
  const muse = {
    id: 'muse',
    displayName: 'Muse',
    description: 'A fluffy cream-colored plush buddy.',
    spritesheetPath: 'spritesheet.webp',
  }
  it('accepts a petdex folder as shared by hand', () => {
    const check = parsePetFolder(muse, { width: 1536, height: 1872 })
    expect(check).toEqual({
      ok: true,
      pet: {
        slug: 'muse',
        displayName: 'Muse',
        description: 'A fluffy cream-colored plush buddy.',
        sheetFile: 'spritesheet.webp',
      },
    })
  })
  it('falls back to the folder name for an id, lower-cased', () => {
    const check = parsePetFolder({ displayName: 'X' }, { width: 1536, height: 1872 }, 'My-Pet')
    expect(check.ok && check.pet.slug).toBe('my-pet')
  })
  it('refuses a bad id, a sheet outside the folder, a missing or odd-sized sheet', () => {
    const size = { width: 1536, height: 1872 }
    expect(parsePetFolder({ id: 'Bad Id!' }, size)).toMatchObject({ ok: false })
    expect(parsePetFolder({ id: 'a', spritesheetPath: '../x.webp' }, size)).toMatchObject({
      ok: false,
    })
    expect(parsePetFolder({ id: 'a', spritesheetPath: 'sheet.png' }, size)).toMatchObject({
      ok: false,
    })
    expect(parsePetFolder({ id: 'a' }, null)).toMatchObject({ ok: false })
    const odd = parsePetFolder({ id: 'a' }, { width: 1000, height: 1000 })
    expect(odd.ok).toBe(false)
    expect(!odd.ok && odd.reason).toMatch(/192×208/)
  })
})

describe('petdex sheets', () => {
  it('reads the frame grid from the pixel size', () => {
    expect(sheetGeometry(1536, 1872)).toEqual({ cols: 8, rows: 9 })
    expect(sheetGeometry(1536, 2288)).toEqual({ cols: 8, rows: 11 })
    expect(sheetGeometry(1728, 1664)).toEqual({ cols: 9, rows: 8 })
    expect(sheetGeometry(1000, 1000)).toBeNull()
  })

  it('counts real frames per row, stopping at the first blank', () => {
    // Cache Capy's sheet: wave has 4 frames, jump 5, idle 7 (capped at 6).
    const rows = [
      [1, 1, 1, 1, 1, 1, 1, 0],
      [1, 1, 1, 1, 0, 0, 0, 0],
      [1, 1, 1, 1, 1, 0, 0, 0],
      [0, 0, 0, 0, 0, 0, 0, 0],
    ]
    const counts = rowFrameCounts({ cols: 8, rows: 4 }, (c, r) => rows[r]![c] === 0)
    expect(counts).toEqual([6, 4, 5, 1])
  })

  it('maps states to rows in both taxonomies', () => {
    expect(petStateRow('idle', 9)).toBe(0)
    expect(petStateRow('wave', 9)).toBe(3)
    expect(petStateRow('jump', 9)).toBe(4)
    expect(petStateRow('failed', 9)).toBe(5)
    expect(petStateRow('waiting', 9)).toBe(6)
    expect(petStateRow('run', 9)).toBe(7)
    expect(petStateRow('review', 9)).toBe(8)
    expect(petStateRow('run', 8)).toBe(2)
    expect(petStateRow('waiting', 8)).toBe(0)
  })

  it('derives the state in Hermes priority order', () => {
    expect(derivePetState({})).toBe('idle')
    expect(derivePetState({ busy: true })).toBe('run')
    expect(derivePetState({ busy: true, awaitingInput: true })).toBe('waiting')
    expect(derivePetState({ awaitingInput: true, justCompleted: true })).toBe('wave')
    expect(derivePetState({ justCompleted: true, celebrate: true })).toBe('jump')
    expect(derivePetState({ celebrate: true, error: true })).toBe('failed')
    expect(derivePetState({ reasoning: true })).toBe('review')
  })

  it('accepts petdex slugs only', () => {
    expect(isPetSlug('cache-capy')).toBe(true)
    expect(isPetSlug('Cache Capy')).toBe(false)
    expect(isPetSlug('../x')).toBe(false)
    expect(isPetSlug('')).toBe(false)
  })
})
