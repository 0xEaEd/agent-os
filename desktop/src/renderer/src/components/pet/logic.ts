// Where the pet sits, as a fraction of the room it has: 0 is the left/top
// edge, 1 the right/bottom edge. A window that grows or shrinks moves the
// pet with it and can never leave it off screen, which absolute pixels did.

export interface Size {
  w: number
  h: number
}

export interface Point {
  x: number
  y: number
}

/** Position as a share of the free space in each axis, 0..1. */
export interface PetAnchor {
  fx: number
  fy: number
}

/** Bottom-right corner, a little in from the edges, where the pet starts. */
export const DEFAULT_ANCHOR: PetAnchor = { fx: 1, fy: 1 }
/** Default inset from the window edges (the old fixed right/bottom offsets). */
export const DEFAULT_INSET: Point = { x: 28, y: 96 }

function clamp01(n: number): number {
  return Number.isFinite(n) ? Math.min(1, Math.max(0, n)) : 0
}

/** The pixels the pet can move through in each axis; 0 when it is bigger than the window. */
function room(size: Size, win: Size): Size {
  return { w: Math.max(0, win.w - size.w), h: Math.max(0, win.h - size.h) }
}

export function anchorFromPixels(pos: Point, size: Size, win: Size): PetAnchor {
  const r = room(size, win)
  return {
    fx: r.w > 0 ? clamp01(pos.x / r.w) : 0,
    fy: r.h > 0 ? clamp01(pos.y / r.h) : 0,
  }
}

/** Whole pixels, always inside the window. */
export function pixelsFromAnchor(anchor: PetAnchor, size: Size, win: Size): Point {
  const r = room(size, win)
  return { x: Math.round(clamp01(anchor.fx) * r.w), y: Math.round(clamp01(anchor.fy) * r.h) }
}

/** Where a pet with no saved spot goes: the default corner, inset. */
export function defaultAnchor(size: Size, win: Size): PetAnchor {
  return anchorFromPixels(
    { x: win.w - size.w - DEFAULT_INSET.x, y: win.h - size.h - DEFAULT_INSET.y },
    size,
    win,
  )
}

/**
 * Read a saved position. New saves are anchors; an older save holds the
 * pixels of the window it was dragged in, converted against the window it
 * is read in (the best guess available, and clamped either way).
 */
export function parseStoredAnchor(raw: unknown, size: Size, win: Size): PetAnchor | null {
  if (!raw || typeof raw !== 'object') return null
  const o = raw as Record<string, unknown>
  if (typeof o.fx === 'number' && typeof o.fy === 'number') {
    return { fx: clamp01(o.fx), fy: clamp01(o.fy) }
  }
  if (typeof o.x === 'number' && typeof o.y === 'number') {
    return anchorFromPixels({ x: o.x, y: o.y }, size, win)
  }
  return null
}

/**
 * What the mascot must never take a click away from.
 *
 * The pet is a 192×208 sprite floating at the top of the stacking order, and
 * its artwork fills that box almost edge to edge — so nothing can be won back
 * by clipping the hit area to the drawn pixels. Anything it happens to stand
 * over simply stopped responding: the composer's route button, the "Inspect
 * tx" and "View transaction" buttons on a ledger card. It is decoration; the
 * app's controls outrank it everywhere.
 */
export const PET_YIELDS_TO =
  'button, a[href], input, select, textarea, summary, label, [role="button"],' +
  ' [role="tab"], [role="menuitem"], [role="menuitemradio"], [role="checkbox"],' +
  ' [role="switch"], [role="link"], [role="option"], [contenteditable="true"],' +
  ' [tabindex]:not([tabindex="-1"])'

/** True when this point of the window belongs to a control, not to the pet. */
export function petYieldsAt(target: Element | null): boolean {
  if (!target) return false
  const control = target.closest(PET_YIELDS_TO)
  // The pet is itself a <button>; it must not read itself as a reason to yield.
  return control !== null && !control.classList.contains('pet')
}

/** Is this point inside the pet's box? */
export function pointInRect(rect: DOMRect, x: number, y: number): boolean {
  return x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom
}
