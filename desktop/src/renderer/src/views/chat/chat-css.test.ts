import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const css = readFileSync('src/renderer/src/views/chat/chat.css', 'utf8')

// Two rules in this stylesheet only work because of a geometry fact that is
// easy to lose in a later edit: on the desktop skin `.msg.user` IS the bubble —
// it carries the padding and the background, and `.msg-body` sits INSIDE that
// padding. Anything positioned from the body's box therefore starts inside the
// bubble, and anything positioned from the column edge hangs outside the
// scroll container. Both bit us once; these guard the fix.
describe('desktop chat CSS geometry contract', () => {
  it('parks the user row hover actions in the outer gutter, not in the bubble padding', () => {
    // The shared rule places actions below the BODY (`top: calc(100% + 4px)`),
    // which is correct for assistant rows — their meta line is that 16px row.
    // On a user row the same offset lands on the bubble's own bottom padding,
    // sitting over the last line of the message and its rounded corner.
    expect(css).toMatch(/\.msg \.msg-body > \.msg-actions \{[\s\S]*?top: calc\(100% \+ 4px\);/)
    const userActions = css.match(/\.msg\.user \.msg-body > \.msg-actions \{[\s\S]*?\n\}/)?.[0]
    expect(userActions).toBeTruthy()
    expect(userActions).toMatch(/top: auto;/)
    expect(userActions).toMatch(/right: 100%;/)
    // Must clear the bubble's own 14px side padding plus a visible gap.
    const margin = Number(userActions?.match(/margin-right: (\d+)px;/)?.[1])
    const bubblePadding = Number(css.match(/\.msg\.user \{[\s\S]*?padding: \d+px (\d+)px;/)?.[1])
    expect(bubblePadding).toBeGreaterThan(0)
    expect(margin).toBeGreaterThan(bubblePadding)

    // …and a hover bridge wide enough to cross that gutter, or the pointer
    // leaves `.msg` on the way to the buttons and they vanish mid-reach.
    const bridge = css.match(/\.msg\.user \.msg-body > \.msg-actions::before \{[\s\S]*?\n\}/)?.[0]
    expect(bridge).toBeTruthy()
    expect(Number(bridge?.match(/width: (\d+)px;/)?.[1])).toBeGreaterThanOrEqual(margin)
  })

  it('reserves enough side padding for the hover timestamp to survive overflow clipping', () => {
    // Rows sit flush with the column edge and `.msg::after` hangs the time
    // OUTSIDE them. The thread clips horizontally, so the side minimum has to
    // cover that overhang — at 24px the stamp was sliced ("13:59" → "13:")
    // whenever the desk panel narrowed the column enough for the minimum to win.
    expect(css).toMatch(/\.msg\.user::after \{[\s\S]*?right: -8px;/)
    expect(css).toMatch(/\.chat-thread \{[\s\S]*?overflow-x: hidden;/)
    const sideMin = Number(css.match(/\.chat-thread \{[\s\S]*?padding: \d+px max\((\d+)px,/)?.[1])
    expect(sideMin).toBeGreaterThanOrEqual(44)
  })

  it('keeps the jump-to-latest dock out of the transcript layout', () => {
    const dock = css.match(/\.chat-jump-dock \{[\s\S]*?\n\}/)?.[0]
    expect(dock).toMatch(/height: 0;/)
    expect(dock).toMatch(/pointer-events: none;/)
    expect(css).toMatch(/\.chat-jump-dock\[data-visible='false'\] \{[\s\S]*?visibility: hidden;/)
  })
})
