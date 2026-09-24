import qrcode from 'qrcode-generator'

/**
 * A QR code drawn HERE, from the bytes given, with no network involved.
 *
 * The point is not convenience — a public QR web service would be less code.
 * It is that handing a wallet address to a third party to have it drawn tells
 * that third party the address, and the picture then fails to load anyway: the
 * desktop window's CSP allows `data:` and `blob:` images but no arbitrary
 * remote host. Encoding locally settles both at once.
 *
 * Error correction M (~15%), the usual choice for a screen: a phone camera has
 * no smudges to recover from, and a lower level keeps the modules large.
 */

// UTF-8, rather than the library's default SJIS table: an address is ASCII,
// but a label next to it need not be, and a wrong byte table is a QR that
// scans back as mojibake rather than one that fails loudly.
qrcode.stringToBytes = (s: string) => Array.from(new TextEncoder().encode(s))

export interface QrOptions {
  /** Rendered edge in CSS pixels. The SVG itself is resolution-independent. */
  size?: number
  /** Dark module colour. Light stays white: a QR needs the contrast to scan. */
  dark?: string
}

/**
 * The QR as a standalone SVG document.
 *
 * White background and a four-module quiet zone are part of the symbol, not
 * decoration — a QR painted straight onto a dark panel does not scan. Modules
 * are one path of 1×1 squares on an integer grid, so scaling stays crisp and
 * the markup stays small enough to inline as a data URI.
 */
export function qrSvg(text: string, options: QrOptions = {}): string {
  const size = options.size ?? 160
  const dark = options.dark ?? '#000000'
  const code = qrcode(0, 'M')
  code.addData(text, 'Byte')
  code.make()
  const modules = code.getModuleCount()
  const quiet = 4
  const edge = modules + quiet * 2
  let path = ''
  for (let row = 0; row < modules; row += 1) {
    for (let col = 0; col < modules; col += 1) {
      if (code.isDark(row, col)) path += `M${col + quiet} ${row + quiet}h1v1h-1z`
    }
  }
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" ` +
    `viewBox="0 0 ${edge} ${edge}" shape-rendering="crispEdges">` +
    `<rect width="${edge}" height="${edge}" fill="#ffffff"/>` +
    `<path d="${path}" fill="${dark}"/>` +
    `</svg>`
  )
}

/**
 * The same QR as a `data:` URI, for an `<img src>`.
 *
 * base64 rather than a percent-encoded SVG: the payload is ASCII either way,
 * but base64 has no characters an attribute or a sanitizer has to think about.
 */
export function qrDataUrl(text: string, options: QrOptions = {}): string {
  const svg = qrSvg(text, options)
  const base64 =
    typeof btoa === 'function'
      ? btoa(svg)
      : Buffer.from(svg, 'binary').toString('base64') /* c8 ignore next */
  return `data:image/svg+xml;base64,${base64}`
}
