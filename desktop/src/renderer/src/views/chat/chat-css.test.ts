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

  it('reserves enough side padding for the gutter timestamp to survive overflow clipping', () => {
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

// Every selector in the stylesheet, one per entry of a selector list (commas
// inside `:is()`/`:not()` stay put).
const selectors = (css.replace(/\/\*[\s\S]*?\*\//g, '').match(/[^{};]+(?=\{)/g) ?? []).flatMap(
  (prelude) => prelude.split(/,(?![^(]*\))/).map((s) => s.trim()),
)

// The turn footer (model, tokens, cache, cost) used to open on hover, from
// nothing to a 16px row plus a 4px margin, and take the glyph room on its
// right at the same moment: every row below jumped a line whenever the pointer
// crossed a message. The gutter timestamp was hover-only too.
describe('desktop chat response info', () => {
  it('keeps the footer and the timestamp on screen at rest, so hover never moves a row', () => {
    const meta = css.match(/^\.msg-meta \{[\s\S]*?^\}/m)?.[0]
    expect(meta).toBeTruthy()
    expect(meta).toMatch(/min-height: 16px;/)
    expect(meta).toMatch(/margin-top: 4px;/)
    expect(meta).not.toMatch(/opacity: 0;|\bheight: 0;|overflow: hidden;/)
    // The copy/retry glyphs park at the footer's trailing end, so their room
    // has to be there before they appear or the figures rewrap under them.
    const actions = css.match(/^\.msg \.msg-body > \.msg-actions \{[\s\S]*?^\}/m)?.[0]
    const glyph = Number(css.match(/^\.msg-action \{[\s\S]*?width: (\d+)px;/m)?.[1])
    const gap = Number(actions?.match(/gap: (\d+)px;/)?.[1])
    expect(glyph).toBeGreaterThan(0)
    expect(Number(meta?.match(/padding-right: (\d+)px;/)?.[1])).toBeGreaterThanOrEqual(
      2 * glyph + gap,
    )

    const stamp = css.match(/^\.msg::after \{[\s\S]*?^\}/m)?.[0]
    expect(stamp).toMatch(/content: attr\(data-time\);/)
    expect(stamp).not.toMatch(/opacity: 0;/)

    // Hovering or focusing a row reveals its actions, which are out of flow,
    // and nothing else.
    expect(actions).toMatch(/position: absolute;[\s\S]*?opacity: 0;/)
    const rowStates = selectors.filter((s) => /\.msg:(hover|focus-within)/.test(s))
    expect(rowStates).toContain('.msg:hover .msg-actions')
    expect(rowStates).toContain('.msg:focus-within .msg-actions')
    expect(rowStates.filter((s) => !s.endsWith(' .msg-actions'))).toEqual([])
    // However a hover state is spelled (`.msg.assistant:hover`, `:is()`), it
    // must not reach the footer or the stamp.
    expect(
      selectors.filter(
        (s) => /:(hover|focus-within)/.test(s) && /\.msg-meta\b|\.msg[^\s]*::after/.test(s),
      ),
    ).toEqual([])
  })

  it('keeps the footer off a streaming row until the turn completes', () => {
    expect(css).toMatch(/^\.msg\.streaming \.msg-meta \{\s*display: none;\s*\}/m)
  })
})

// The file chip is the console's markup (`file · name · mime`) drawn as a
// card: the shared renderer stamps `data-artifact-kind`, `-summary` and
// `-action` for exactly this, and the skin must keep reading them — the raw
// mime span is hidden, not reworded, and `.msg-body a` must not win the
// anchor back (it underlines it in lime).
describe('desktop artifact file card', () => {
  const card = css.match(/^\.msg-body \.msg-artifact-chip \{[\s\S]*?^\}/m)?.[0]

  it('outranks the transcript link rule and lays the card out as a grid', () => {
    expect(card).toBeTruthy()
    expect(card).toMatch(/display: grid;/)
    expect(card).toMatch(/text-decoration: none;/)
    expect(card).toMatch(/grid-template-areas:\s*'tile name action'\s*'tile summary action';/)
  })

  it('draws the subtitle and the button from the renderer-stamped attributes', () => {
    expect(css).toMatch(
      /^\.msg-body \.msg-artifact-chip::before \{[\s\S]*?content: attr\(data-artifact-summary\);/m,
    )
    expect(css).toMatch(
      /^\.msg-body \.msg-artifact-chip::after \{[\s\S]*?content: attr\(data-artifact-action\);/m,
    )
    expect(css).toMatch(/^\.msg-artifact-chip \.msg-file-chip__meta \{\s*display: none;\s*\}/m)
  })

  it('tints the tile per kind, with a glyph for every kind the renderer emits', () => {
    const tinted = selectors.filter((s) => /\.msg-artifact-chip\[data-artifact-kind=/.test(s))
    for (const kind of ['spreadsheet', 'document', 'pdf', 'presentation', 'archive', 'data']) {
      const rule = css.match(
        new RegExp(`\\.msg-artifact-chip\\[data-artifact-kind='${kind}'\\] \\{[\\s\\S]*?\\n\\}`),
      )?.[0]
      expect(rule, kind).toMatch(/--artifact-accent: var\(--(ok|info|danger|warn)\);/)
      expect(rule, kind).toMatch(/--artifact-glyph: url\('data:image\/svg\+xml,/)
    }
    expect(tinted.length).toBeGreaterThanOrEqual(6)
    // The glyph is a mask so it takes the accent, never a fixed colour.
    expect(css).toMatch(
      /^\.msg-artifact-chip \.msg-file-chip__icon::before \{[\s\S]*?mask: var\(--artifact-glyph\) center \/ contain no-repeat;/m,
    )
  })
})

// LP cards (frontend lp.ts) are the console's markup; the desktop draws them as
// desk instruments through the `data-lp-*` hooks the renderer stamps. These pin
// the hooks the skin depends on and the parts that make it the desk's, not the
// console's: a status rail rather than a border, figures in mono, and an
// explorer link that `.msg-body a` cannot repaint.
/**
 * Columns `repeat(auto-fit, minmax(clamp(A, (700px - 100%) * 999, B), 1fr))`
 * yields for four stats in a strip `width` px wide with a `gap` px column gap:
 * the most tracks n such that n·min + (n − 1)·gap ≤ width, capped at 4.
 */
function stripColumns(block: string, width: number, gap: number): number {
  const px = (v: string): number => (v.endsWith('rem') ? parseFloat(v) * 16 : parseFloat(v))
  const m =
    /minmax\(clamp\(calc\((\d+)% - ([\d.]+(?:px|rem))\), calc\(\((\d+)px - 100%\) \* (\d+)\), calc\((\d+)% - ([\d.]+(?:px|rem))\)\), 1fr\)/.exec(
      block.replace(/\s+/g, ' '),
    )
  if (!m) throw new Error('strip columns are not the quarter/half clamp')
  const lo = (Number(m[1]) / 100) * width - px(m[2]!)
  const flip = (Number(m[3]) - width) * Number(m[4])
  const hi = (Number(m[5]) / 100) * width - px(m[6]!)
  const min = Math.min(Math.max(flip, lo), hi)
  return Math.min(4, Math.floor((width + gap) / (min + gap)))
}

describe('desktop LP card skin', () => {
  const card = css.match(/^\.lp-card \{[\s\S]*?^\}/m)?.[0]

  it('draws the plate with a hairline and a status rail, not a border', () => {
    expect(card).toBeTruthy()
    expect(card).toMatch(/inset 3px 0 0 var\(--lp-rail\)/)
    expect(card).not.toMatch(/\bborder:/)
    expect(card).toMatch(/font-variant-numeric: tabular-nums;/)
  })

  it('tones the rail and the pill from the renderer-stamped status', () => {
    for (const status of ['in-range', 'above-range', 'below-range']) {
      expect(selectors, status).toContain(`.lp-card[data-lp-status='${status}']`)
      expect(selectors, status).toContain(`.lp-pill[data-lp-status='${status}']`)
    }
    expect(css).toMatch(/\.lp-card\[data-lp-status='in-range'\] \{\s*--lp-rail: var\(--ok\);/)
  })

  it('sets every figure in mono and keeps lime for the live bar', () => {
    expect(css).toMatch(
      /\.lp-stat__value,[\s\S]*?\.lp-row__value,[\s\S]*?font-family: var\(--font-mono\);/,
    )
    expect(css).toMatch(
      /\.lp-chart__bar\[data-active='true'\] \.lp-chart__fill \{\s*fill: var\(--primary\);/,
    )
  })

  it('outranks the transcript link rule for the explorer action', () => {
    const action = css.match(/^\.msg-body \.lp-card__action \{[\s\S]*?^\}/m)?.[0]
    expect(action).toMatch(/text-decoration: none;/)
    expect(action).toMatch(/color: var\(--muted-foreground\);/)
  })

  it('never changes the case of a token symbol', () => {
    // The pair and the price caption both carry symbols ("boar", "WETH per boar").
    for (const rule of ['.lp-card__pair', '.lp-chart__caption']) {
      const block = css.match(new RegExp(`^\\${rule} \\{[\\s\\S]*?^\\}`, 'm'))?.[0]
      expect(block, rule).toBeTruthy()
      expect(block, rule).not.toMatch(/text-transform/)
    }
  })

  it('keys the layout on the card, never on the host wrapper', () => {
    expect(selectors.some((s) => s.includes('.msg-artifact-lp[data-lp-kind'))).toBe(false)
    expect(selectors).toContain(".lp-card[data-lp-kind='positions']")
  })

  it('keeps position rows at desk density with fees and distance columns', () => {
    const rule = (selector: string): string | undefined =>
      css.match(
        new RegExp(`^${selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')} \\{[\\s\\S]*?^\\}`, 'm'),
      )?.[0]
    const columns = (block: string | undefined): string[] | undefined =>
      block
        ?.match(/grid-template-columns:\s*([^;]+);/)?.[1]
        ?.trim()
        .split(/\s+(?![^(]*\))/)
    const row = rule('.lp-row')
    expect(row).toMatch(/min-height: 26px;/)
    // Every row shares the book's columns, so they line up and size to content.
    expect(row).toMatch(/grid-template-columns: subgrid;/)
    // Stacked (default, < 760px): two 26px-or-less lines —
    // status+distance | pair | value over chain | owner | fees.
    expect(row).toMatch(/grid-template-rows: 26px 22px;/)
    const book = rule('.lp-card .lp-rows')
    expect(columns(book)).toEqual(['max-content', 'minmax(0, 1fr)', 'max-content'])
    // Outranks `.msg-body :is(ul, ol)`, which indented the book by 1.3em.
    expect(book).toMatch(/padding: 0;/)
    expect(css).toMatch(/\.lp-card \.lp-row \+ \.lp-row \{\s*margin-top: 0;/)
    // Dense (the mounter measured >= 760px): one 26px line, six columns.
    expect(rule(".lp-card[data-lp-layout='dense'] .lp-row")).toMatch(/grid-template-rows: 26px;/)
    expect(columns(rule(".lp-card[data-lp-layout='dense'] .lp-rows"))).toHaveLength(6)
    expect(selectors).toContain('.lp-row__distance')
    expect(selectors).toContain('.lp-row__fees')
  })

  it('gives a narrow card (< 440px) three lines per row and one fact per line', () => {
    const rule = (selector: string): string | undefined =>
      css.match(
        new RegExp(`^${selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')} \\{[\\s\\S]*?^\\}`, 'm'),
      )?.[0]
    const n = ".lp-card[data-lp-layout='narrow']"
    expect(rule(`${n} .lp-rows`)).toMatch(/grid-template-columns: minmax\(0, 1fr\);/)
    const row = rule(`${n} .lp-row`)
    expect(row).toMatch(/grid-template-columns: max-content minmax\(0, 1fr\) max-content;/)
    // Three lines; the pair's line grows if a long symbol wraps.
    expect(row).toMatch(/grid-template-rows: 24px minmax\(20px, auto\) 20px;/)
    // Rows no longer inherit the book's column gap, so they carry their own
    // (without it "ROBINHOOD CHAIN" ran into the owner).
    expect(row).toMatch(/column-gap: 10px;/)
    expect(rule(`${n} .lp-row__status`)).toMatch(/grid-area: 1 \/ 1 \/ 2 \/ 3;/)
    expect(rule(`${n} .lp-row__value`)).toMatch(/grid-area: 1 \/ 3;/)
    const pair = rule(`${n} .lp-row__pair`)
    expect(pair).toMatch(/grid-area: 2 \/ 1 \/ 3 \/ 3;/)
    expect(pair).toMatch(/overflow: visible;/)
    expect(pair).toMatch(/white-space: normal;/)
    expect(rule(`${n} .lp-row__fees`)).toMatch(/grid-area: 2 \/ 3;/)
    expect(rule(`${n} .lp-row__chain`)).toMatch(/grid-area: 3 \/ 1;/)
    expect(rule(`${n} .lp-row__wallet`)).toMatch(/grid-area: 3 \/ 2 \/ 4 \/ 4;/)
    // Facts one per line (label left, figure right), smaller figures.
    expect(css).toMatch(
      /\.lp-card\[data-lp-layout='narrow'\] \.lp-card__stats,\s*\.lp-card\[data-lp-layout='narrow'\] \.lp-card__stats\.lp-totals \{\s*grid-template-columns: minmax\(0, 1fr\);/,
    )
    expect(rule(`${n} .lp-stat__value`)).toMatch(/font-size: 11px;/)
    expect(css).not.toMatch(/data-lp-layout='narrow'\][^{]*\{[^}]*text-overflow: ellipsis/)
  })

  it('never cuts what a row means: only the owner may ellipsize', () => {
    const ellipsized = [...css.matchAll(/^(\.lp-row[^{]*) \{[^}]*text-overflow: ellipsis/gm)].map(
      (m) => m[1],
    )
    expect(ellipsized).toEqual(['.lp-row__wallet'])
    const status = css.match(/^\.lp-row__status \{[\s\S]*?^\}/m)?.[0]
    expect(status).not.toMatch(/overflow: hidden/)
    // The fixed 140px status column clipped "−100.0%" to "-100.".
    expect(css).not.toMatch(/grid-template-columns: 140px/)
  })

  it('styles the measured label states the mounter stamps', () => {
    expect(selectors).toContain(".lp-range[data-lp-bounds='stacked'] .lp-range__upper")
    expect(css).toMatch(/\.lp-range\[data-lp-now-wrap\] \.lp-range__now-side \{\s*display: block;/)
    expect(css).toMatch(/\.lp-chart__tick\[data-lp-hidden\] \{\s*visibility: hidden;/)
  })

  it('draws the link arrow only on an owner that is a link', () => {
    expect(selectors).not.toContain('.lp-row__wallet[data-lp-external]::before')
    expect(selectors).toContain('.lp-row__wallet[data-lp-link]::after')
    expect(selectors).toContain('.msg-body .lp-row__wallet[data-lp-link]')
  })

  it('lays the positions strip out 4-up from 700px and 2×2 below, never 3 + 1', () => {
    const strip = css.match(/^\.lp-card__stats\.lp-totals \{[\s\S]*?^\}/m)?.[0]
    expect(strip).toBeTruthy()
    const gap = 18 // .lp-card__stats gap: 0 18px
    // The live report: a 608px card (581px strip) wrapped FEES onto its own line.
    expect(stripColumns(strip!, 581, gap)).toBe(2)
    expect(stripColumns(strip!, 699, gap)).toBe(2)
    expect(stripColumns(strip!, 700, gap)).toBe(4)
    expect(stripColumns(strip!, 1000, gap)).toBe(4)
    for (let w = 240; w <= 1400; w += 1) expect(stripColumns(strip!, w, gap)).not.toBe(3)
  })

  it('greens unclaimed fees only when there are some', () => {
    expect(css).toMatch(/^\.lp-row__fees \{[^}]*color: var\(--muted-foreground\);/m)
    expect(css).toMatch(/^\.lp-row__fees\[data-lp-fees='positive'\] \{\s*color: var\(--ok\);/m)
    expect(css).toMatch(
      /^\.lp-hero\[data-lp-hero='fees'\] \.lp-hero__value\[data-lp-fees='positive'\] \{\s*color: var\(--ok\);/m,
    )
    expect(css).toMatch(
      /^\.lp-hero\[data-lp-hero='fees'\] \.lp-hero__value\[data-lp-fees='zero'\] \{\s*color: var\(--muted-foreground\);/m,
    )
    expect(css).not.toMatch(/lp-hero__value:not\(\[data-lp-no-price\]\)/)
  })

  it('shows both copy outcomes', () => {
    expect(css).toMatch(/\.lp-card__copy\[data-lp-copied='true'\] \{\s*color: var\(--ok\);/)
    expect(css).toMatch(/\.lp-card__copy\[data-lp-copied='failed'\] \{\s*color: var\(--danger\);/)
  })

  it('hangs the range bounds on the band edges, above the rule', () => {
    expect(css).toMatch(/\.lp-range__lower,\s*\.lp-range__upper \{[\s\S]*?position: absolute;/)
    expect(css).toMatch(/\.lp-range__upper \{\s*transform: translateX\(-100%\);/)
  })
})
