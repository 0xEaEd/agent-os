import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

// Comments stripped, so prose that mentions a property never matches as one.
const css = readFileSync('src/renderer/src/views/projects/projects.css', 'utf8').replace(
  /\/\*[\s\S]*?\*\//g,
  '',
)

/** Any property that draws a line around a box; `border-radius` only rounds it. */
const RING = /box-shadow|outline|\bborder(?!-radius)[\w-]*:/

/** The body of the first rule whose selector list is exactly `selector`. */
function rule(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const body = css.match(new RegExp(`(?:^|\\n)${escaped} \\{([\\s\\S]*?)\\n\\}`))?.[1]
  expect(body, `no rule for ${selector}`).toBeTruthy()
  return body ?? ''
}

/** Every rule on the row itself (`.proj-new`, its states and pseudo-elements), not its parts. */
function rowRules(): Array<[string, string]> {
  return [...css.matchAll(/(?:^|\n)(\.proj-new(?!__)[^{]*)\{([\s\S]*?)\n\}/g)].map((m) => [
    (m[1] ?? '').trim(),
    m[2] ?? '',
  ])
}

describe('sidebar new-project row CSS', () => {
  it('draws no ring around the row, so the focused field shows a single outline', () => {
    const rules = rowRules()
    expect(rules.map(([selector]) => selector)).toContain('.proj-new')
    // Busy only dims it; no state, pseudo-element or later rule brings a ring back.
    for (const [selector, body] of rules) {
      expect(body, selector).not.toMatch(RING)
    }
    // The tint stays: it is what makes the icon, field and hint read as one group.
    expect(rule('.proj-new')).toMatch(
      /background: color-mix\(in srgb, var\(--sidebar-accent\) 70%, transparent\);/,
    )
  })

  it("keeps the field's own hairline and its focus ring", () => {
    expect(rule('.proj-new__input')).toMatch(/box-shadow: 0 0 0 0\.5px var\(--input\);/)
    const focus = rule('.proj-new__input:focus')
    expect(focus).toMatch(/0 0 0 0\.5px var\(--input\)/)
    expect(focus).toMatch(/0 0 0 3px color-mix\(in srgb, var\(--ring\) 32%, transparent\)/)
  })
})
