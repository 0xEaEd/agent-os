/**
 * The pixel mark that stands for a wallet, drawn HERE from its address.
 *
 * Same reasoning as the QR in `@/lib/qr`: handing an address to a public
 * identicon service tells that service which address you are looking at, and
 * the picture fails to load anyway — the window's CSP allows `data:` images
 * and no arbitrary remote host. A hash of the address is all the art needs.
 *
 * The shape is the familiar Ethereum "blockie": an 8×8 grid, the left half
 * drawn from a seeded PRNG and mirrored onto the right, so the mark is
 * symmetric and reads as a face rather than as noise. Two colours over a
 * plate, all three seeded from the same address, so one wallet always gets
 * one mark and two wallets are told apart at 24 px.
 */

/** The classic grid. Even, so the mirror has no seam column. */
const GRID = 8

/**
 * xorshift128, seeded by folding the address into four words.
 *
 * The widely-copied blockies code divides by 2^31 and so returns values in
 * [0, 2) — a bug its callers absorb by clamping. Dividing by 2^32 gives the
 * [0, 1) this file's callers assume, which keeps hues inside the colour wheel
 * and the cell distribution at the intended ~43/43/13.
 */
function seeded(seed: string): () => number {
  const words = [0, 0, 0, 0]
  for (let i = 0; i < seed.length; i += 1) {
    const k = i % 4
    words[k] = ((words[k]! << 5) - words[k]! + seed.charCodeAt(i)) | 0
  }
  let [a, b, c, d] = words as [number, number, number, number]
  return () => {
    const t = (a ^ (a << 11)) | 0
    a = b
    b = c
    c = d
    d = (d ^ (d >>> 19) ^ t ^ (t >>> 8)) | 0
    return (d >>> 0) / 4294967296
  }
}

/**
 * A colour for the desk, not for a white page: hue is free, but lightness is
 * held inside a band so no mark comes out as a black square on a near-black
 * panel or as a white one that outshines the value beside it.
 */
function pick(rand: () => number, lightFrom: number, lightTo: number): string {
  const h = Math.round(rand() * 360)
  const s = Math.round(45 + rand() * 40)
  const l = Math.round(lightFrom + rand() * (lightTo - lightFrom))
  return `hsl(${h} ${s}% ${l}%)`
}

/** The 8×8 cells: 0 plate, 1 colour, 2 spot — left half drawn, right mirrored. */
function cells(rand: () => number): number[] {
  const half = GRID / 2
  const out: number[] = []
  for (let y = 0; y < GRID; y += 1) {
    const row: number[] = []
    for (let x = 0; x < half; x += 1) row.push(Math.floor(rand() * 2.3))
    out.push(...row, ...[...row].reverse())
  }
  return out
}

/** Every cell of one value as a single path of 1×1 squares on an integer grid. */
function pathFor(grid: number[], value: number): string {
  let d = ''
  for (let i = 0; i < grid.length; i += 1) {
    if (grid[i] !== value) continue
    d += `M${i % GRID} ${Math.floor(i / GRID)}h1v1h-1z`
  }
  return d
}

/**
 * The mark as a standalone SVG document. Square and un-rounded: the corner
 * radius belongs to the element that shows it, so the same art can sit in a
 * 40 px header tile and a 16 px menu row.
 */
export function blockieSvg(address: string, size = 40): string {
  // Addresses are compared case-insensitively everywhere else on the desk;
  // the mark has to agree, or a checksummed address grows a second identity.
  const rand = seeded(address.trim().toLowerCase())
  // Draw order is part of the seed: colour, plate, spot, then the cells.
  const colour = pick(rand, 52, 72)
  const plate = pick(rand, 16, 30)
  const spot = pick(rand, 58, 78)
  const grid = cells(rand)
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" ` +
    `viewBox="0 0 ${GRID} ${GRID}" shape-rendering="crispEdges">` +
    `<rect width="${GRID}" height="${GRID}" fill="${plate}"/>` +
    `<path d="${pathFor(grid, 1)}" fill="${colour}"/>` +
    `<path d="${pathFor(grid, 2)}" fill="${spot}"/>` +
    `</svg>`
  )
}

/** The same mark as a `data:` URI, for an `<img src>`. */
export function blockieDataUrl(address: string, size = 40): string {
  const svg = blockieSvg(address, size)
  const base64 =
    typeof btoa === 'function'
      ? btoa(svg)
      : Buffer.from(svg, 'binary').toString('base64') /* c8 ignore next */
  return `data:image/svg+xml;base64,${base64}`
}
