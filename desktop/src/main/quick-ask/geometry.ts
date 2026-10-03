/** A rectangle in screen points, the shape of Electron's `Rectangle`. */
export interface Rect {
  x: number
  y: number
  width: number
  height: number
}

/** The panel's width in CSS px; at a UI scale it grows with the zoom. */
export const PANEL_WIDTH = 640
/** Content height before the view has measured itself. */
export const PANEL_INITIAL_HEIGHT = 112
/** Never closer than this to a display edge. */
const MARGIN = 24
/** Where the panel's top edge sits, as a fraction of the work area: Spotlight's spot. */
const TOP_FRACTION = 0.22

/**
 * Where the panel goes on a display: centred horizontally, its top a fifth
 * of the way down, growing downwards as the field grows. `contentHeight` is
 * in CSS px and `zoom` is the page's zoom factor, so a UI scale of 125%
 * gives a panel 25% larger whose layout is unchanged. Clamped to the work
 * area so a small display or a large scale never pushes it off screen.
 */
export function panelBounds(workArea: Rect, contentHeight: number, zoom = 1): Rect {
  const z = Number.isFinite(zoom) && zoom > 0 ? zoom : 1
  const width = Math.max(1, Math.min(Math.round(PANEL_WIDTH * z), workArea.width - 2 * MARGIN))
  const height = Math.max(1, Math.min(Math.round(contentHeight * z), workArea.height - 2 * MARGIN))
  const x = workArea.x + Math.round((workArea.width - width) / 2)
  const top = workArea.y + Math.round(workArea.height * TOP_FRACTION)
  const lowest = workArea.y + workArea.height - MARGIN - height
  const y = Math.max(workArea.y + MARGIN, Math.min(top, lowest))
  return { x, y, width, height }
}
