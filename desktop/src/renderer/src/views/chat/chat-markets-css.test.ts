import { readFileSync } from 'node:fs'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createArtifactRenderer } from '@/views/chat/transcript/artifacts'
import {
  MARKETS_ARTIFACT_MIME,
  buildMarketsCard,
  createMarketsMounter,
  normalizeMarketsPayload,
  type MarketsPayload,
} from '@/views/chat/transcript/markets'

// The desktop skins the shared markets card (frontend markets.ts) in its own
// chat.css. The live test of 2026-10-06 found the skin styling only the
// contract's hook classes: the renderer's structural classes (`.mk-cols`,
// `.mk-market`, `.mk-pricing`, …) had no rules, so the column head read
// "PairTVLVol 24hPrice24hAge", columns shifted per row and "$0.1134" ran into
// "0.000711 NVDA". These tests render the REAL card and hold the skin to it,
// so the DOM and the stylesheet cannot drift apart silently.

const css = readFileSync('src/renderer/src/views/chat/chat.css', 'utf8')
const FIXTURE = 'src/renderer/src/views/trading/__fixtures__/markets/nvda.json'
const NOW = Date.parse('2026-10-06T04:14:00Z')
const ZERO = '0x0000000000000000000000000000000000000000'

/** The NVDA read, with every optional branch of the card switched on. */
function rawPayload(): Record<string, unknown> {
  const raw = JSON.parse(readFileSync(FIXTURE, 'utf8')) as {
    sections: { quote: Record<string, unknown>[]; base: Record<string, unknown>[] }
    [key: string]: unknown
  }
  const ai = raw.sections.quote[0]!
  const weth = raw.sections.base[1]!
  const cp = ai.counterparty as Record<string, unknown>
  // A lookalike row (wears its pill and the counterparty name after the pair).
  const lookalike = {
    ...ai,
    poolAddress: '0x9999999999999999999999999999999999999999',
    pair: 'NVDA/NVDA',
    counterparty: { ...cp, symbol: 'NVDA', name: 'Nvda Inu', lookalike: true },
  }
  // The native coin on a base row (ETH, never "0x000…").
  const eth = {
    ...weth,
    poolAddress: '0x8888888888888888888888888888888888888888',
    pair: 'NVDA/WETH',
    counterparty: {
      ...(weth.counterparty as Record<string, unknown>),
      address: ZERO,
      symbol: 'ETH',
      name: 'Ether',
      native: true,
    },
    swap: { chainId: 4663, tokenIn: (raw.token as { address: string }).address, tokenOut: ZERO },
  }
  return {
    ...raw,
    partial: true,
    warnings: ['GeckoTerminal rate limit: showing the first 80 pools'],
    counts: { ...(raw.counts as object), limited: 12, pageCapHit: true },
    sections: { quote: [...raw.sections.quote, lookalike], base: [...raw.sections.base, eth] },
  }
}

function payload(): MarketsPayload {
  const p = normalizeMarketsPayload(rawPayload())
  if (!p) throw new Error('fixture did not normalize')
  return p
}

function card(): HTMLElement {
  return buildMarketsCard(payload(), { now: () => NOW, canRefresh: true, canSwap: true })
}

const classesOf = (el: Element): string[] => [...el.classList]

/** Every class name used anywhere under `root`. */
function allClasses(root: Element): Set<string> {
  const out = new Set<string>(classesOf(root))
  root.querySelectorAll('*').forEach((el) => classesOf(el).forEach((c) => out.add(c)))
  return out
}

/** A rule in chat.css selects this class (not just a longer class that starts with it). */
function styled(cls: string): boolean {
  return new RegExp(`\\.${cls.replace(/[-]/g, '\\-')}(?![\\w-])`).test(css)
}

/** One `--mk-cols` value's track count: top-level space-separated terms. */
function trackCount(value: string): number {
  let depth = 0
  let tracks = 0
  let inTrack = false
  for (const ch of value.trim()) {
    if (ch === '(') depth++
    if (ch === ')') depth--
    if (/\s/.test(ch) && depth === 0) {
      inTrack = false
      continue
    }
    if (!inTrack) {
      tracks++
      inTrack = true
    }
  }
  return tracks
}

/** The `--mk-cols` declared inside the rule for `selector` in `block`. */
function colsIn(block: string, selector: string): string {
  const esc = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const rule = block.match(new RegExp(`(?:^|\\n)\\s*${esc} \\{([\\s\\S]*?)\\n\\s*\\}`))?.[1]
  const value = rule?.match(/--mk-cols:\s*([^;]+);/)?.[1]
  if (!value) throw new Error(`no --mk-cols in ${selector}`)
  return value
}

/** The body of the narrow container query. */
const narrow =
  css.match(/@container mk-card \(max-width: 420px\) \{([\s\S]*?)\n\}/)?.[1] ??
  (() => {
    throw new Error('no narrow container query')
  })()
const wide = css.slice(0, css.indexOf('@container mk-card (max-width: 420px)'))

describe('markets card · the renderer’s real DOM', () => {
  it('draws the structural classes the desktop skin lays out', () => {
    const root = card()
    for (const selector of [
      '.mk-head',
      '.mk-section[data-side="quote"] .mk-cols',
      '.mk-section[data-side="base"] .mk-cols',
      '.mk-row .mk-market .mk-pair',
      '.mk-row .mk-market .mk-cp-name',
      '.mk-row .mk-market .mk-venue .mk-dex .mk-version',
      '.mk-row .mk-venue .mk-flags .mk-flag[data-kind="lookalike"]',
      '.mk-row .mk-pricing .mk-px',
      '.mk-row .mk-pricing .mk-px-in',
      '.mk-row[data-side="base"] .mk-pricing .mk-premium',
      '.mk-row .mk-swap',
      '.mk-warnings .mk-warning',
      '.mk-foot .mk-meta .mk-counts',
      '.mk-foot .mk-meta .mk-partial',
      '.mk-foot .mk-meta .mk-ago',
      '.mk-foot .mk-actions .mk-link[data-action="deep"]',
      '.mk-foot .mk-actions .mk-link[data-action="lookalikes"]',
      '.mk-foot .mk-actions .mk-refresh',
    ]) {
      expect(root.querySelector(selector), selector).not.toBeNull()
    }
  })

  it('heads each section with one column per row cell, in the row’s order', () => {
    // The skin puts `.mk-cols` and `.mk-row` on ONE grid template; that only
    // lines up while the head and the row list their cells in the same order.
    const root = card()
    root.querySelectorAll('.mk-cols').forEach((head) => {
      expect(
        [...head.children].map((c) => classesOf(c).find((k) => k.startsWith('mk-col--'))),
      ).toEqual([
        'mk-col--pair',
        'mk-col--tvl',
        'mk-col--vol',
        'mk-col--px',
        'mk-col--change',
        'mk-col--age',
      ])
    })
    root.querySelectorAll('.mk-row').forEach((row) => {
      expect([...row.children].map((c) => classesOf(c)[0])).toEqual([
        'mk-market',
        'mk-tvl',
        'mk-vol',
        'mk-pricing',
        'mk-change',
        'mk-age',
        'mk-swap',
      ])
    })
  })

  it('keeps the USD price and the price in the other token in separate elements', () => {
    const root = card()
    const ai = root.querySelector<HTMLElement>('.mk-section[data-side="quote"] .mk-row')!
    expect(ai.querySelector('.mk-px')?.textContent).toBe('$0.1131')
    expect(ai.querySelector('.mk-px-in')?.textContent).toMatch(/ NVDA$/)
    const usdg = root.querySelector<HTMLElement>('.mk-section[data-side="base"] .mk-row')!
    expect(usdg.querySelector('.mk-premium')?.textContent).toMatch(/vs oracle$/)
    // The premium is a line of the price cell, never the change cell's.
    expect(usdg.querySelector('.mk-change .mk-premium')).toBeNull()
  })

  it('names a native counterparty ETH and a same-symbol one by name', () => {
    const root = card()
    const rows = [...root.querySelectorAll<HTMLElement>('.mk-row')]
    const eth = rows.find((r) => r.dataset.pool === '0x8888888888888888888888888888888888888888')!
    expect(eth.querySelector('.mk-pair')?.textContent).toBe('NVDA/ETH')
    expect(eth.textContent).not.toMatch(/0x0000/)
    const twin = rows.find((r) => r.dataset.lookalike === 'true')!
    expect(twin.querySelector('.mk-cp-name')?.textContent).toBe('Nvda Inu')
  })
})

describe('markets card · the desktop skin in chat.css', () => {
  afterEach(() => {
    document.body.replaceChildren()
  })

  it('styles every class the transcript placeholder and the mounted card emit', async () => {
    // The real path: the artifact renderer's placeholder, mounted by the real
    // mounter, then a failed ↻ for the transient error line.
    const container = document.createElement('div')
    container.className = 'msg-body'
    container.innerHTML = createArtifactRenderer({
      ensureStreamBubble: () => container,
      markVisibleStreamEvent: () => {},
      scrollToBottom: () => {},
      getAutoScroll: () => false,
      getStreamBubble: () => container,
      pushStreamArtifact: () => {},
      getStreamArtifacts: () => [],
      getSessionKey: () => 'agent:desk:test',
      getAuthToken: () => 'tok',
      esc: (s) => s,
    }).renderArtifacts([
      {
        id: 'mk-1',
        name: 'markets-NVDA.json',
        mime: MARKETS_ARTIFACT_MIME,
        download_url: '/a/mk-1',
      },
    ])
    document.body.append(container)
    const placeholder = allClasses(container)
    const mounter = createMarketsMounter({
      fetchPayload: async () => rawPayload(),
      call: async () => {
        throw new Error('gateway down')
      },
      onSwap: () => {},
      now: () => NOW,
    })
    mounter.mountMarkets(container)
    await vi.waitFor(() => expect(container.querySelector('.mk-card')).not.toBeNull())
    container.querySelector<HTMLButtonElement>('.mk-refresh')!.click()
    await vi.waitFor(() => expect(container.querySelector('.mk-error')).not.toBeNull())
    const emitted = new Set([...placeholder, ...allClasses(container)])
    mounter.destroyAll()

    const ours = [...emitted].filter(
      (c) => c.startsWith('mk-') || c.startsWith('msg-artifact-markets'),
    )
    // Sanity: the walk saw the structural classes the live test found unstyled.
    for (const cls of [
      'msg-artifact-markets-group',
      'msg-artifact-markets',
      'msg-artifact-markets__body',
      'msg-artifact-markets__status',
      'mk-cols',
      'mk-col',
      'mk-col--pair',
      'mk-col--tvl',
      'mk-col--vol',
      'mk-col--px',
      'mk-col--change',
      'mk-col--age',
      'mk-market',
      'mk-venue',
      'mk-pricing',
      'mk-meta',
      'mk-ago',
      'mk-actions',
      'mk-error',
      'mk-warnings',
      'mk-warning',
    ]) {
      expect(ours, cls).toContain(cls)
    }
    const unstyled = ours.filter((cls) => !styled(cls))
    expect(unstyled, `classes the renderer emits that chat.css never styles`).toEqual([])
  })

  it('lays the head and every row on one grid template, with a track per column', () => {
    expect(css).toMatch(
      /\.mk-cols,\n\.mk-row \{[\s\S]*?display: grid;[\s\S]*?grid-template-columns: var\(--mk-cols\);/,
    )
    // Six columns; seven when the rows carry Swap.
    expect(trackCount(colsIn(wide, '.mk-card'))).toBe(6)
    expect(trackCount(colsIn(wide, '.mk-section:has(.mk-swap)'))).toBe(7)
  })

  it('drops Vol and Age, head and cells alike, in a card 420 px wide or less', () => {
    expect(css).toMatch(/\.msg-artifact-markets \{[\s\S]*?container: mk-card \/ inline-size;/)
    expect(narrow).toMatch(
      /\.mk-col--vol,\s*\.mk-col--age,\s*\.mk-vol,\s*\.mk-age \{\s*display: none;/,
    )
    expect(trackCount(colsIn(narrow, '.mk-card'))).toBe(4)
    expect(trackCount(colsIn(narrow, '.mk-section:has(.mk-swap)'))).toBe(5)
  })

  it('stacks the price cell, and never truncates the price in the other token', () => {
    const pricing = css.match(/\n\.mk-pricing \{([\s\S]*?)\n\}/)?.[1] ?? ''
    expect(pricing).toMatch(/display: flex;/)
    expect(pricing).toMatch(/flex-direction: column;/)
    const pxIn = css.match(/\n\.mk-px-in \{([\s\S]*?)\n\}/)?.[1] ?? ''
    expect(pxIn).toMatch(/overflow-wrap: anywhere;/)
    expect(pxIn).not.toMatch(/text-overflow: ellipsis/)
    expect(pxIn).not.toMatch(/white-space: nowrap/)
  })
})
