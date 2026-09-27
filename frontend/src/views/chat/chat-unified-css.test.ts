import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const css = readFileSync('src/views/chat/chat-unified.css', 'utf8')
const legacyCss = readFileSync('src/views/chat/chat.css', 'utf8')

describe('unified Chat CSS contract', () => {
  it('uses the product type system while preserving mono machine data', () => {
    expect(css).toMatch(/\.chat-surface \.chat-stage \{[\s\S]*?font-family: var\(--font-sans\);/)
    expect(css).toMatch(/\.chat-surface \.msg-meta \{[\s\S]*?font-family: var\(--font-mono\);/)
    expect(css).toMatch(/\.chat-surface \.chat-tools-summary \{/)
  })

  it('replaces the perpetual neon composer animation with a stable focus surface', () => {
    expect(css).toMatch(/\.chat-surface \.chat-composer \{[\s\S]*?animation: none;/)
    expect(css).toMatch(
      /\.chat-surface \.chat-composer::before,[\s\S]*?\.chat-surface \.chat-composer::after \{[\s\S]*?content: none;/,
    )
    expect(css).toMatch(/\.chat-surface \.chat-composer:focus-within \{/)
  })

  it('keeps the large transcript stationary and coordinates only lightweight entry surfaces', () => {
    expect(css).toMatch(
      /\.chat-surface \.chat-thread \{[\s\S]*?overflow-anchor: none;[\s\S]*?scrollbar-gutter: stable;/,
    )
    expect(css).toMatch(
      /\.chat-view-enter \.chat-composer-shell \{[\s\S]*?animation: chat-composer-enter/,
    )
    expect(css).toMatch(
      /\.shell\[data-surface='chat'\] \.shell-chat-header \{[\s\S]*?animation: chat-header-enter/,
    )
    expect(css).toMatch(
      /@media \(prefers-reduced-motion: reduce\) \{[\s\S]*?\.chat-view-enter \.chat-composer-shell,[\s\S]*?animation: none !important;/,
    )
  })

  it('keeps history out of the paint tree until positioned and reserves image geometry', () => {
    expect(css).toMatch(
      /\.chat-surface \.chat-thread\[data-history-ready='false'\] \{[\s\S]*?visibility: hidden;/,
    )
    expect(css).toMatch(
      /\.chat-surface \.chat-thread\[data-history-ready='true'\] \+ \.chat-history-loading \{[\s\S]*?display: none;/,
    )
    expect(css).toMatch(
      /\.chat-surface \.msg-artifact-preview,[\s\S]*?aspect-ratio: 16 \/ 10;[\s\S]*?object-fit: contain;/,
    )
  })

  it('reserves chart geometry and overlays the status so a drawn chart cannot shift the transcript', () => {
    // The canvas is filled after a lazy import plus a payload fetch, so its
    // height must exist before either resolves.
    expect(css).toMatch(/\.chat-surface \.msg-artifact-chart__canvas \{[\s\S]*?height: 20rem;/)
    // Canvas and status share one grid cell — hiding the status must not
    // collapse a row.
    expect(css).toMatch(
      /\.chat-surface \.msg-artifact-chart__canvas,[\s\S]*?\.chat-surface \.msg-artifact-chart__status \{[\s\S]*?grid-row: 3;[\s\S]*?grid-column: 1;/,
    )
    expect(css).toMatch(/\.chat-surface \.msg-artifact-charts \{[\s\S]*?max-width: 100%;/)
  })

  it('reserves the crosshair readout its own row so the first hover cannot shift the chart', () => {
    // The strip is empty until a chart draws, so its height has to exist
    // before the first crosshair move fills it.
    expect(css).toMatch(
      /\.chat-surface \.msg-artifact-chart__readout \{[\s\S]*?grid-row: 2;[\s\S]*?min-height: 1\.125rem;/,
    )
    // Three rows: header, readout, then the canvas cell.
    expect(css).toMatch(
      /\.chat-surface \.msg-artifact-chart \{[\s\S]*?grid-template-rows: auto auto 1fr;/,
    )
    // The strip must not swallow the crosshair it is reporting on.
    expect(css).toMatch(
      /\.chat-surface \.msg-artifact-chart__readout \{[\s\S]*?pointer-events: none;/,
    )
  })

  it('reserves portalled header geometry before reactive controls mount', () => {
    expect(css).toMatch(
      /\.shell-chat-header__context \{[\s\S]*?min-height: 2\.5rem;[\s\S]*?overflow: visible;/,
    )
    expect(css).toMatch(
      /\.shell-chat-header__primary-action \{[\s\S]*?min-width: 6\.75rem;[\s\S]*?min-height: 2\.5rem;/,
    )
    expect(css).toMatch(
      /@media \(max-width: 768px\)[\s\S]*?\.shell-chat-header \{[\s\S]*?min-height: 6\.25rem;/,
    )
  })

  it('keeps lime as a signal instead of filling the user message bubble', () => {
    const userBubble = css.match(/\.chat-surface \.msg\.user \{([\s\S]*?)\n\}/)?.[1] ?? ''
    expect(userBubble).toContain('background: var(--elevated)')
    expect(userBubble).not.toContain('background: var(--primary)')
  })

  it('keeps the Chat toolbar compact while widening popovers and mobile targets', () => {
    expect(css).toMatch(/\.chat-session-popover \{[\s\S]*?width: min\(30rem,/)
    expect(css).toMatch(
      /\.shell-chat-header \{[\s\S]*?width: min\(62rem, calc\(100% - 1rem\)\);[\s\S]*?min-height: 3\.25rem;[\s\S]*?grid-template-columns: auto minmax\(18rem, 1fr\) auto;/,
    )
    expect(css).toMatch(/\.chat-toolbar-popover \{[\s\S]*?width: min\(32rem,/)
    expect(css).toMatch(/@media \(max-width: 768px\)[\s\S]*?min-height: 2\.75rem;/)
    expect(css).toMatch(
      /@media \(max-width: 768px\)[\s\S]*?\.chat-session-popover \{[\s\S]*?position: absolute;[\s\S]*?top: calc\(100% \+ 0\.625rem\);/,
    )
    expect(css).toMatch(
      /@media \(max-width: 560px\)[\s\S]*?\.chat-composer__input,[\s\S]*?font-size: 1rem;/,
    )
  })

  it('keeps the floating Chat header above the transcript stacking context', () => {
    expect(css).toMatch(
      /\.shell\[data-surface='chat'\] \.shell-chat-header \{[\s\S]*?position: relative;[\s\S]*?z-index: 30;[\s\S]*?border-radius: var\(--radius-surface\);/,
    )
    expect(css).toMatch(/\.shell-chat-header__context \{[\s\S]*?overflow: visible;/)
  })

  it('positions the actions menu from a trigger-sized anchor instead of the full header row', () => {
    expect(css).toMatch(/\.chat-session-actions \{[\s\S]*?position: relative;[\s\S]*?flex: none;/)
    expect(css).toMatch(
      /\.chat-session-actions-menu \{[\s\S]*?top: calc\(100% \+ 0\.625rem\);[\s\S]*?right: 0;/,
    )
  })

  it('keeps keyboard focus visible inside header menus and session results', () => {
    expect(css).toMatch(
      /\.chat-session-actions-menu__item:focus-visible \{[\s\S]*?outline: 2px solid var\(--ring\);/,
    )
    expect(css).toMatch(
      /\.chat-session-popover-item:focus-visible \{[\s\S]*?outline: 2px solid var\(--ring\);/,
    )
  })

  it('keeps run status textual instead of reducing it to a color-only dot', () => {
    expect(css).toMatch(
      /@media \(max-width: 560px\)[\s\S]*?\.chat-session-run-status__compact \{[\s\S]*?display: inline;/,
    )
    expect(css).not.toMatch(/\.chat-session-run-status \{[\s\S]{0,300}?font-size: 0;/)
  })

  it('mirrors the unified palette into portalled Chat dialogs', () => {
    expect(css).toMatch(
      /:root\[data-theme='dark'\] :is\(\.chat-modal-overlay, \.chat-output-modal-overlay\)/,
    )
    expect(css).toMatch(
      /:root\[data-theme='light'\] :is\(\.chat-modal-overlay, \.chat-output-modal-overlay\)/,
    )
  })

  it('uses semantic radii for Chat controls, surfaces, and dialogs', () => {
    expect(css).not.toMatch(/border-radius:\s*\d+px/)
    expect(css).toMatch(
      /\.chat-surface \.chat-tools-collapse \{[\s\S]*?border-radius: var\(--radius-control\);/,
    )
    expect(css).toMatch(
      /\.chat-surface \.chat-slash-item \{[\s\S]*?border-radius: var\(--radius-control\);/,
    )
    expect(css).toMatch(
      /\.chat-session-popover-item \{[\s\S]*?border-radius: var\(--radius-control\);/,
    )
    expect(css).toMatch(/\.chat-modal \{[\s\S]*?border-radius: var\(--radius-dialog\);/)
  })

  it('keeps only the clipped popover-search seam square', () => {
    expect(css.match(/border-radius:\s*0;/g)).toHaveLength(1)
    expect(css).toMatch(
      /\.chat-session-popover-search \{[\s\S]*?border-radius: 0;[\s\S]*?background: var\(--surface\);/,
    )
    expect(legacyCss).toMatch(
      /\.chat-tools-collapse \{[\s\S]*?border-radius: var\(--radius-control\);/,
    )
    expect(legacyCss).toMatch(
      /\.chat-attachments__rejection \{[\s\S]*?border-radius: var\(--radius-control\);/,
    )
  })
})

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

describe('LP card CSS contract', () => {
  const block = (selector: string): string | undefined =>
    css.match(
      new RegExp(`^${selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')} \\{[\\s\\S]*?^\\}`, 'm'),
    )?.[0]

  it('never changes the case of a token symbol', () => {
    const pair = block('.chat-surface .lp-card__pair')
    expect(pair).toBeTruthy()
    expect(pair).not.toMatch(/text-transform/)
    expect(block('.chat-surface .lp-chart__caption')).not.toMatch(/text-transform/)
  })

  it('hangs the range bounds on the band edges and styles both copy outcomes', () => {
    expect(css).toMatch(
      /\.chat-surface \.lp-range__lower,\s*\.chat-surface \.lp-range__upper \{[\s\S]*?position: absolute;/,
    )
    expect(block(".chat-surface .lp-card__copy[data-lp-copied='true']")).toMatch(/var\(--ok\)/)
    expect(block(".chat-surface .lp-card__copy[data-lp-copied='failed']")).toMatch(
      /var\(--danger\)/,
    )
  })

  it('lays position rows out in two lines until the card measures dense, clipping only the owner', () => {
    // Stacked (the default): status | pair+owner | value+fees, shared by every row.
    expect(block('.chat-surface .lp-rows')).toMatch(
      /grid-template-columns: max-content minmax\(0, 1fr\) max-content;/,
    )
    const row = block('.chat-surface .lp-row')
    expect(row).toMatch(/grid-template-columns: subgrid;/)
    expect(row).toMatch(/grid-column: 1 \/ -1;/)
    // Dense: six columns, only behind the mounter's measured stamp.
    const dense = block(".chat-surface .lp-card[data-lp-layout='dense'] .lp-rows")
    expect(
      dense
        ?.match(/grid-template-columns:\s*([^;]+);/)?.[1]
        ?.trim()
        .split(/\s+(?![^(]*\))/),
    ).toHaveLength(6)
    expect(block('.chat-surface .lp-row__chain')).toMatch(/grid-area: 2 \/ 1;/)
    expect(css).toMatch(/^\.chat-surface \.lp-row__wallet \{\s*grid-area: 2 \/ 2;/m)
    expect(block('.chat-surface .lp-row__fees')).toMatch(/grid-area: 2 \/ 3;/)
    expect(block(".chat-surface .lp-card[data-lp-layout='dense'] .lp-row__value")).toMatch(
      /grid-area: 1 \/ 6;/,
    )
    // Only the owner may ellipsize; the status, distance, pair and figures never do.
    const ellipsized = [
      ...css.matchAll(/^(\.chat-surface \.lp-row[^{]*) \{[^}]*text-overflow: ellipsis/gm),
    ].map((m) => m[1])
    expect(ellipsized).toEqual(['.chat-surface .lp-row__wallet'])
    expect(block('.chat-surface .lp-row__status')).not.toMatch(/overflow: hidden/)
    // The phone rule no longer hides the chain, owner and fees: line two has room.
    expect(css).not.toMatch(/\.lp-row__chain,\s*\.chat-surface \.lp-row__wallet,[^}]*display: none/)
  })

  it('gives a narrow card (< 440px) three lines per row, nothing overlapping or cut', () => {
    const n = ".chat-surface .lp-card[data-lp-layout='narrow']"
    // Rows stop sharing the book's columns: each sizes its own three.
    expect(block(`${n} .lp-rows`)).toMatch(/grid-template-columns: minmax\(0, 1fr\);/)
    const row = block(`${n} .lp-row`)
    expect(row).toMatch(/grid-template-columns: max-content minmax\(0, 1fr\) max-content;/)
    expect(row).toMatch(/column-gap:/)
    // Line 1: status · distance | value. Line 2: pair · fee | fees. Line 3: chain | owner.
    expect(block(`${n} .lp-row__status`)).toMatch(/grid-area: 1 \/ 1 \/ 2 \/ 3;/)
    expect(block(`${n} .lp-row__value`)).toMatch(/grid-area: 1 \/ 3;/)
    const pair = block(`${n} .lp-row__pair`)
    expect(pair).toMatch(/grid-area: 2 \/ 1 \/ 3 \/ 3;/)
    // The pair wraps rather than clipping under the value.
    expect(pair).toMatch(/overflow: visible;/)
    expect(pair).toMatch(/white-space: normal;/)
    expect(block(`${n} .lp-row__fees`)).toMatch(/grid-area: 2 \/ 3;/)
    expect(block(`${n} .lp-row__chain`)).toMatch(/grid-area: 3 \/ 1;/)
    expect(block(`${n} .lp-row__wallet`)).toMatch(/grid-area: 3 \/ 2 \/ 4 \/ 4;/)
    // Strip: 2×2 with smaller figures.
    expect(block(`${n} .lp-card__stats.lp-totals`)).toMatch(
      /grid-template-columns: repeat\(2, minmax\(0, 1fr\)\);/,
    )
    expect(block(`${n} .lp-stat__value`)).toMatch(/font-size: 0\.8125rem;/)
    // Still only the owner ellipsizes.
    expect(css).not.toMatch(/data-lp-layout='narrow'\][^{]*\{[^}]*text-overflow: ellipsis/)
  })

  it('styles the measured label states the mounter stamps', () => {
    expect(block(".chat-surface .lp-range[data-lp-bounds='stacked'] .lp-range__upper")).toMatch(
      /bottom: calc\(100% \+/,
    )
    expect(block('.chat-surface .lp-range[data-lp-now-wrap] .lp-range__now-side')).toMatch(
      /display: block;/,
    )
    expect(block('.chat-surface .lp-range[data-lp-now-wrap] .lp-range__row')).toMatch(
      /padding-bottom:/,
    )
    expect(block('.chat-surface .lp-chart__tick[data-lp-hidden]')).toMatch(/visibility: hidden;/)
  })

  it('lays the positions strip out 4-up from 700px and 2×2 below, never 3 + 1', () => {
    const strip = block('.chat-surface .lp-card__stats.lp-totals')
    expect(strip).toBeTruthy()
    const gap = 8 // .lp-card__stats gap: 0.5rem
    // The live report: a 608px card (576px strip) wrapped FEES onto its own line.
    expect(stripColumns(strip!, 576, gap)).toBe(2)
    expect(stripColumns(strip!, 699, gap)).toBe(2)
    expect(stripColumns(strip!, 700, gap)).toBe(4)
    expect(stripColumns(strip!, 1000, gap)).toBe(4)
    for (let w = 240; w <= 1400; w += 1) expect(stripColumns(strip!, w, gap)).not.toBe(3)
  })

  it('greens unclaimed fees only when there are some', () => {
    expect(block('.chat-surface .lp-row__fees')).toMatch(/color: var\(--muted-foreground\);/)
    expect(block(".chat-surface .lp-row__fees[data-lp-fees='positive']")).toMatch(
      /color: var\(--ok\);/,
    )
    expect(
      block(".chat-surface .lp-hero[data-lp-hero='fees'] .lp-hero__value[data-lp-fees='positive']"),
    ).toMatch(/color: var\(--ok\);/)
    expect(
      block(".chat-surface .lp-hero[data-lp-hero='fees'] .lp-hero__value[data-lp-fees='zero']"),
    ).toMatch(/color: var\(--muted-foreground\);/)
    // No rule greens a fees figure merely for having a price.
    expect(css).not.toMatch(/lp-hero__value:not\(\[data-lp-no-price\]\)/)
  })

  it('keeps card links out of the transcript link colour', () => {
    expect(css).toContain(
      '.chat-surface .msg-body .lp-card a:is(.lp-card__action, .lp-row__wallet) {',
    )
    expect(css).toContain('.chat-surface a.lp-row__wallet::after {')
    expect(css).not.toMatch(/\.msg-artifact-lp\[data-lp-kind/)
  })
})
