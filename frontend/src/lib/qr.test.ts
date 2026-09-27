import { describe, expect, it } from 'vitest'
import { qrDataUrl, qrSvg } from './qr'

const ADDRESS = '0x89e0fA1B2c3D4e5F60718293a4b5C6d7E8f9da97'

/** The modules of the symbol, as a grid, read back out of the rendered path. */
function grid(svg: string): boolean[][] {
  const edge = Number(/viewBox="0 0 (\d+)/.exec(svg)![1])
  const cells = Array.from({ length: edge }, () => Array.from({ length: edge }, () => false))
  for (const m of svg.matchAll(/M(\d+) (\d+)h1v1h-1z/g)) {
    cells[Number(m[2])]![Number(m[1])] = true
  }
  return cells
}

describe('qrSvg', () => {
  it('encodes an address as a version-3 symbol with a four-module quiet zone', () => {
    const svg = qrSvg(ADDRESS)
    // 29 modules (version 3) + 4 quiet on each side. The quiet zone is part of
    // the symbol: a QR drawn flush to the edge of a dark panel does not scan.
    expect(svg).toContain('viewBox="0 0 37 37"')
    const cells = grid(svg)
    for (let i = 0; i < 37; i += 1) {
      expect(cells[0]![i]).toBe(false)
      expect(cells[i]![0]).toBe(false)
      expect(cells[36]![i]).toBe(false)
      expect(cells[i]![36]).toBe(false)
    }
  })

  it('draws the three finder patterns the scanner locks onto', () => {
    const cells = grid(qrSvg(ADDRESS))
    // A finder is a 7×7 ring: dark border, light gap, 3×3 dark core.
    const finder = (top: number, left: number) => {
      for (let i = 0; i < 7; i += 1) {
        expect(cells[top]![left + i]).toBe(true)
        expect(cells[top + 6]![left + i]).toBe(true)
        expect(cells[top + i]![left]).toBe(true)
        expect(cells[top + i]![left + 6]).toBe(true)
      }
      expect(cells[top + 1]![left + 1]).toBe(false)
      expect(cells[top + 3]![left + 3]).toBe(true)
    }
    finder(4, 4)
    finder(4, 4 + 29 - 7)
    finder(4 + 29 - 7, 4)
  })

  it('keeps a white background, because a QR needs the contrast to scan', () => {
    expect(qrSvg(ADDRESS)).toContain('fill="#ffffff"')
  })

  it('grows the symbol with the payload rather than truncating it', () => {
    const small = Number(/viewBox="0 0 (\d+)/.exec(qrSvg('hi'))![1])
    const big = Number(/viewBox="0 0 (\d+)/.exec(qrSvg('x'.repeat(400)))![1])
    expect(big).toBeGreaterThan(small)
  })

  it('takes the requested pixel size without changing the module grid', () => {
    const svg = qrSvg(ADDRESS, { size: 240 })
    expect(svg).toContain('width="240"')
    expect(svg).toContain('viewBox="0 0 37 37"')
  })
})

describe('qrDataUrl', () => {
  it('inlines the symbol, so no third party is asked to draw the address', () => {
    const url = qrDataUrl(ADDRESS)
    expect(url.startsWith('data:image/svg+xml;base64,')).toBe(true)
    expect(atob(url.slice('data:image/svg+xml;base64,'.length))).toContain('viewBox="0 0 37 37"')
  })
})
