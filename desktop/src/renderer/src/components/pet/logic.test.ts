import { describe, expect, it } from 'vitest'
import {
  anchorFromPixels,
  defaultAnchor,
  parseStoredAnchor,
  petYieldsAt,
  pixelsFromAnchor,
  pointInRect,
} from './logic'

const pet = { w: 64, h: 69 }
const win = { w: 1000, h: 700 }

describe('pet anchor', () => {
  it('round-trips a pixel position through the free-space fraction', () => {
    const anchor = anchorFromPixels({ x: 468, y: 315.5 }, pet, win)
    expect(anchor.fx).toBeCloseTo(0.5)
    expect(anchor.fy).toBeCloseTo(0.5)
    expect(pixelsFromAnchor(anchor, pet, win)).toEqual({ x: 468, y: 316 })
  })

  it('moves the pet with the window and keeps it inside a smaller one', () => {
    // Dragged to the bottom-right corner of a big window…
    const anchor = anchorFromPixels({ x: 936, y: 631 }, pet, win)
    expect(anchor).toEqual({ fx: 1, fy: 1 })
    // …it is still in the corner, and on screen, after the window shrinks.
    expect(pixelsFromAnchor(anchor, pet, { w: 400, h: 300 })).toEqual({ x: 336, y: 231 })
  })

  it('clamps pixels that are already off screen', () => {
    expect(anchorFromPixels({ x: -50, y: 5000 }, pet, win)).toEqual({ fx: 0, fy: 1 })
  })

  it('pins to the origin when the pet is bigger than the window', () => {
    expect(pixelsFromAnchor({ fx: 1, fy: 1 }, { w: 500, h: 500 }, { w: 300, h: 300 })).toEqual({
      x: 0,
      y: 0,
    })
  })

  it('starts in the bottom-right corner, inset from the edges', () => {
    expect(pixelsFromAnchor(defaultAnchor(pet, win), pet, win)).toEqual({ x: 908, y: 535 })
  })

  it('reads a new anchor, converts an old pixel save, rejects junk', () => {
    expect(parseStoredAnchor({ fx: 0.25, fy: 2 }, pet, win)).toEqual({ fx: 0.25, fy: 1 })
    expect(parseStoredAnchor({ x: 936, y: 631 }, pet, win)).toEqual({ fx: 1, fy: 1 })
    expect(parseStoredAnchor(null, pet, win)).toBeNull()
    expect(parseStoredAnchor({ x: 'a' }, pet, win)).toBeNull()
  })
})

describe('the pet never takes a click from a control', () => {
  // The sprite is 192×208 and its artwork fills that box almost edge to edge,
  // so nothing is won back by clipping the hit area to the drawn pixels. The
  // pet is `pointer-events: none` and a window listener decides each press;
  // these are the two questions that listener asks.
  function at(html: string): Element {
    document.body.innerHTML = html
    return document.body.firstElementChild as Element
  }

  it('yields to anything the user can operate', () => {
    expect(petYieldsAt(at('<button>Swap</button>'))).toBe(true)
    expect(petYieldsAt(at('<a href="#x">tx</a>'))).toBe(true)
    expect(petYieldsAt(at('<input />'))).toBe(true)
    expect(petYieldsAt(at('<div role="tab">Orders</div>'))).toBe(true)
    expect(petYieldsAt(at('<div tabindex="0">focusable</div>'))).toBe(true)
    // Nested: the press lands on the glyph inside the button.
    document.body.innerHTML = '<button data-testid="b"><svg></svg></button>'
    expect(petYieldsAt(document.querySelector('svg'))).toBe(true)
  })

  it('does not yield to plain content, or to itself', () => {
    expect(petYieldsAt(at('<p>a transcript line</p>'))).toBe(false)
    expect(petYieldsAt(at('<div class="chat-thread"></div>'))).toBe(false)
    expect(petYieldsAt(null)).toBe(false)
    // The pet IS a <button>. Reading itself as a control would make it
    // permanently ungrabbable.
    expect(petYieldsAt(at('<button class="pet app-no-drag"></button>'))).toBe(false)
    // …including a press that starts on something inside the pet.
    expect(petYieldsAt(at('<div tabindex="-1">inert</div>'))).toBe(false)
  })

  it('knows its own box, edges included', () => {
    const r = { left: 100, top: 50, right: 292, bottom: 258 } as DOMRect
    expect(pointInRect(r, 200, 150)).toBe(true)
    expect(pointInRect(r, 100, 50)).toBe(true)
    expect(pointInRect(r, 292, 258)).toBe(true)
    expect(pointInRect(r, 99, 150)).toBe(false)
    expect(pointInRect(r, 200, 259)).toBe(false)
  })
})
