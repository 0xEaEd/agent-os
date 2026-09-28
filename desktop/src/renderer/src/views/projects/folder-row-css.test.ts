import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const css = readFileSync('src/renderer/src/views/projects/projects.css', 'utf8')

/** The body of the first rule whose selector list is exactly `selector`. */
function rule(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const body = css.match(new RegExp(`(?:^|\\n)${escaped} \\{([\\s\\S]*?)\\n\\}`))?.[1]
  expect(body, `no rule for ${selector}`).toBeTruthy()
  return body ?? ''
}

describe('sidebar folder row "+" CSS', () => {
  it('hides the "+" without taking it out of the layout, so the count never shifts', () => {
    const plus = rule('.proj-folder__new')
    expect(plus).toMatch(/opacity: 0;/)
    // Hidden, it must not catch a click meant for the row.
    expect(plus).toMatch(/pointer-events: none;/)
    expect(plus).not.toMatch(/display: none/)
    // Same size as the "+" beside the Projects header.
    expect(plus).toMatch(/width: 18px;/)
    expect(rule('.proj-folders__add')).toMatch(/width: 18px;/)
  })

  it('reveals it on hover, on keyboard focus inside the row, and while the menu is open', () => {
    // :has(:focus-visible), not :focus-within: a mouse click leaves focus on
    // the link, and the "+" must not stay up on that row after the pointer goes.
    expect(css).not.toMatch(/:focus-within \.proj-folder__new/)
    const reveal = rule(
      [
        '.proj-folder__row:hover .proj-folder__new,',
        '.proj-folder__row:has(:focus-visible) .proj-folder__new,',
        ".proj-folder__row[data-menu='true'] .proj-folder__new",
      ].join('\n'),
    )
    expect(reveal).toMatch(/opacity: 1;/)
    expect(reveal).toMatch(/pointer-events: auto;/)
  })

  it('keeps the "+" out of the "Move here" corner during a drag, still holding its place', () => {
    expect(rule(".proj-folder[data-drop='true'] .proj-folder__new")).toMatch(/visibility: hidden;/)
  })

  it('draws the same focus ring as the header "+"', () => {
    expect(rule('.proj-folder__new:focus-visible')).toBe(rule('.proj-folders__add:focus-visible'))
  })

  it("rings the whole row, the link's click target, on keyboard focus", () => {
    expect(rule('a.proj-folder__link:focus-visible::after')).toMatch(
      /outline: 2px solid var\(--sidebar-ring\);/,
    )
  })

  it('lets the whole row open the page while both buttons stay clickable above it', () => {
    expect(rule('.proj-folder__row')).toMatch(/position: relative;/)
    expect(rule('a.proj-folder__link::after')).toMatch(/inset: 0;/)
    for (const button of ['.proj-folder__disclose', '.proj-folder__new']) {
      expect(rule(button)).toMatch(/position: relative;\n\s+z-index: 1;/)
    }
  })
})
