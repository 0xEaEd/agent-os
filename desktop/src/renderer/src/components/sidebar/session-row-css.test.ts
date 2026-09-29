import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { PALETTES } from '~/theme/palettes'

// Comments stripped, so prose that mentions a property never matches as one.
const css = readFileSync('src/renderer/src/theme/tokens.css', 'utf8').replace(
  /\/\*[\s\S]*?\*\//g,
  '',
)

/** Any property that draws a line around a box; `border-radius` only rounds it. */
const RING = /box-shadow|outline|\bborder(?!-radius)[\w-]*:/

const SELECTED = ".mac-session[data-selected='true']"
/** The row itself in some state: not a part of it (the "…" button) nor a pseudo-element. */
const ROW = /^\.mac-session(?:\[[^\]]*\]|:[\w-]+)*$/
const SELECTED_RULE = `${SELECTED}, .mac-session-item[data-menu='true'] ${SELECTED}`

/** Every top-level `.mac-session…` rule, selector list whitespace-collapsed. */
function sessionRules(): Array<[string, string]> {
  return [...css.matchAll(/\n {2}(\.mac-session[^{]*)\{([\s\S]*?)\n {2}\}/g)].map((m) => [
    (m[1] ?? '').replace(/\s+/g, ' ').trim(),
    m[2] ?? '',
  ])
}

/** The body of the first rule whose selector list is exactly `selector`. */
function rule(selector: string): string {
  const body = sessionRules().find(([s]) => s === selector)?.[1]
  expect(body, `no rule for ${selector}`).toBeTruthy()
  return body ?? ''
}

/** The `N%` of `--primary` a rule mixes into its background. */
function tint(selector: string): number {
  const pct = rule(selector).match(
    /background: color-mix\(in srgb, var\(--primary\) (\d+)%, transparent\);/,
  )
  expect(pct, `no primary tint on ${selector}`).toBeTruthy()
  return Number(pct?.[1])
}

function rgb(hex: string): number[] {
  expect(hex, 'palette tokens are #rrggbb').toMatch(/^#[0-9a-f]{6}$/i)
  return [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16))
}

/** CIELAB ΔE*76 between two sRGB colours; about 2.3 is just noticeable. */
function deltaE(a: number[], b: number[]): number {
  const lab = (c: number[]): number[] => {
    const [r, g, bl] = c.map((v) => {
      const s = v / 255
      return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
    }) as [number, number, number]
    const f = (t: number): number => (t > 0.008856 ? Math.cbrt(t) : 7.787 * t + 16 / 116)
    const x = f((r * 0.4124 + g * 0.3576 + bl * 0.1805) / 0.95047)
    const y = f(r * 0.2126 + g * 0.7152 + bl * 0.0722)
    const z = f((r * 0.0193 + g * 0.1192 + bl * 0.9505) / 1.08883)
    return [116 * y - 16, 500 * (x - y), 200 * (y - z)]
  }
  const [p, q] = [lab(a), lab(b)]
  return Math.hypot(...p.map((v, i) => v - (q[i] ?? 0)))
}

/** `color-mix(in srgb, top pct%, transparent)` painted over `under`. */
const over = (top: number[], pct: number, under: number[]): number[] =>
  top.map((v, i) => (v * pct) / 100 + (under[i] ?? 0) * (1 - pct / 100))

describe('sidebar session row CSS', () => {
  it('draws no outline around a selected row, hovered or with its menu open', () => {
    // Every rule a selected row can pick up in those states; the open chat's
    // own grey and the live ring (a pseudo-element) are other features.
    const rules = sessionRules().filter(
      ([s]) =>
        /data-selected|:hover|data-menu/.test(s) &&
        s.split(', ').some((one) => ROW.test(one.split(' ').at(-1) ?? '')),
    )
    const selectors = rules.map(([s]) => s)
    expect(selectors).toContain(SELECTED_RULE)
    expect(selectors).toContain(`${SELECTED}[aria-current='page']`)
    expect(selectors).toContain(
      ".mac-session:hover, .mac-session-item[data-menu='true'] .mac-session",
    )
    // Fills only, so a run of adjacent selected rows never stacks into boxed pills.
    for (const [selector, body] of rules) {
      expect(body, selector).not.toMatch(RING)
    }
  })

  it('keeps a selection apart from the open chat by its tint alone, in every palette', () => {
    const selected = tint(SELECTED_RULE)
    const selectedOpen = tint(`${SELECTED}[aria-current='page']`)
    expect(rule(".mac-session[aria-current='page']")).toMatch(
      /background: var\(--sidebar-accent\);/,
    )
    // Over the opaque --sidebar, as with Reduce transparency on. The default
    // sidebar lets some window vibrancy through, which shifts every fill alike.
    for (const [id, palette] of Object.entries(PALETTES)) {
      for (const mode of ['light', 'dark'] as const) {
        const tokens = palette[mode]
        const sidebar = rgb(tokens.sidebar)
        const primary = rgb(tokens.primary)
        const sel = over(primary, selected, sidebar)
        const where = `${id}/${mode}`
        // A selected row vs the open chat's grey, and vs a selected open chat.
        expect(deltaE(sel, rgb(tokens['sidebar-accent'])), where).toBeGreaterThan(4)
        expect(deltaE(sel, over(primary, selectedOpen, sidebar)), where).toBeGreaterThan(4)
      }
    }
  })
})
