import { describe, expect, it } from 'vitest'
import { blockieDataUrl, blockieSvg } from './blockie'

const A = '0x1111111111111111111111111111111111111111'
const B = '0x89e0fA1B2c3D4e5F60718293a4b5C6d7E8f9da97'

describe('blockie · the mark that stands for a wallet', () => {
  it('draws the same mark for the same address, every time', () => {
    expect(blockieSvg(A)).toBe(blockieSvg(A))
  })

  it('reads a checksummed address as the same wallet as its lowercase form', () => {
    expect(blockieSvg(B)).toBe(blockieSvg(B.toLowerCase()))
    expect(blockieSvg(B)).toBe(blockieSvg(`  ${B}  `))
  })

  it('tells two wallets apart', () => {
    expect(blockieSvg(A)).not.toBe(blockieSvg(B))
  })

  it('mirrors the left half onto the right, so the mark is symmetric', () => {
    // Every drawn cell must have a partner at the mirrored column.
    const svg = blockieSvg(B)
    const cells = [...svg.matchAll(/M(\d) (\d)h1v1h-1z/g)].map(([, x, y]) => `${x},${y}`)
    for (const cell of cells) {
      const [x, y] = cell.split(',')
      expect(cells).toContain(`${7 - Number(x)},${y}`)
    }
    expect(cells.length).toBeGreaterThan(0)
  })

  it('keeps every colour inside the wheel and off the extremes of lightness', () => {
    // A hue over 360 or a near-black plate are the two ways the widely-copied
    // blockies code fails on a dark panel; neither may come back here.
    for (const seed of [A, B, '0xdeadbeef', 'b', '']) {
      for (const [, h, , l] of blockieSvg(seed).matchAll(/hsl\((\d+) (\d+)% (\d+)%\)/g)) {
        expect(Number(h)).toBeLessThanOrEqual(360)
        expect(Number(l)).toBeGreaterThanOrEqual(16)
        expect(Number(l)).toBeLessThanOrEqual(78)
      }
    }
  })

  it('hands back a local data: URI, never a remote one', () => {
    const url = blockieDataUrl(B, 24)
    expect(url.startsWith('data:image/svg+xml;base64,')).toBe(true)
    expect(atob(url.slice('data:image/svg+xml;base64,'.length))).toContain('width="24"')
  })
})
