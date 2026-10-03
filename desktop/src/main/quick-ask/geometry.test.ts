// @vitest-environment node
import { describe, expect, it } from 'vitest'
import { PANEL_WIDTH, panelBounds } from './geometry'

const laptop = { x: 0, y: 25, width: 1512, height: 920 }
// A second display to the left of the main one, with negative coordinates.
const external = { x: -2560, y: -200, width: 2560, height: 1415 }

describe('panelBounds', () => {
  it('centres the panel horizontally, a fifth of the way down', () => {
    const b = panelBounds(laptop, 112)
    expect(b.width).toBe(PANEL_WIDTH)
    expect(b.height).toBe(112)
    expect(b.x).toBe(Math.round((1512 - 640) / 2))
    expect(b.y).toBe(25 + Math.round(920 * 0.22))
  })

  it('lands on whichever display it is given, wherever that display sits', () => {
    const b = panelBounds(external, 112)
    expect(b.x).toBe(-2560 + Math.round((2560 - 640) / 2))
    expect(b.y).toBe(-200 + Math.round(1415 * 0.22))
  })

  it('keeps its top edge as the field grows', () => {
    expect(panelBounds(laptop, 260).y).toBe(panelBounds(laptop, 112).y)
  })

  it('scales with the UI zoom', () => {
    const b = panelBounds(laptop, 100, 1.25)
    expect(b.width).toBe(800)
    expect(b.height).toBe(125)
  })

  it('never leaves a small display', () => {
    const tiny = { x: 0, y: 0, width: 500, height: 300 }
    const b = panelBounds(tiny, 400, 1.5)
    expect(b.x).toBeGreaterThanOrEqual(0)
    expect(b.y).toBeGreaterThanOrEqual(0)
    expect(b.x + b.width).toBeLessThanOrEqual(500)
    expect(b.y + b.height).toBeLessThanOrEqual(300)
  })

  it('ignores a nonsense zoom', () => {
    expect(panelBounds(laptop, 100, 0).width).toBe(PANEL_WIDTH)
    expect(panelBounds(laptop, 100, Number.NaN).width).toBe(PANEL_WIDTH)
  })
})
