import { existsSync, readFileSync, readdirSync } from 'node:fs'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  LP_ARTIFACT_MIME,
  LP_CLOCK_MS,
  LP_COPIED_MS,
  LP_DENSE_MIN_PX,
  LP_NARROW_MAX_PX,
  RANGE_BAND,
  buildChartModel,
  buildLpCard,
  chooseAxis,
  createLpMounter,
  explorerUrl,
  fitLabel,
  layoutLpCard,
  layoutRangeLabels,
  lpLayoutFor,
  visibleTicks,
  formatPrice,
  formatShare,
  formatSignedPct,
  formatSmall,
  formatTokenAmount,
  formatUsd,
  formatUsdCompact,
  isLpArtifact,
  normalizeLpPayload,
  positionMarker,
  signedDistance,
  relativeTime,
  safeExplorerBase,
  shortAddress,
  type LpPayload,
  type LpPoolPayload,
  type LpPosition,
  type LpPositionPayload,
  type LpPositionsPayload,
  type LpRangesPayload,
  type LpRenderContext,
} from './lp'

const FIXTURES = 'src/views/chat/transcript/__fixtures__/lp'
// The engine writes its own payloads here (tests/fixtures/lp_cards); when they
// exist the renderer must read them too, so the two sides cannot drift.
const ENGINE_FIXTURES = '../tests/fixtures/lp_cards'

function fixture(name: string): unknown {
  return JSON.parse(readFileSync(`${FIXTURES}/${name}.json`, 'utf8'))
}

function payload<T extends LpPayload>(name: string): T {
  const normalized = normalizeLpPayload(fixture(name))
  if (!normalized) throw new Error(`fixture ${name} did not normalize`)
  return normalized as T
}

const FETCHED_AT = Date.parse('2026-09-27T09:30:00Z')

function ctx(overrides: Partial<LpRenderContext> = {}): LpRenderContext {
  return {
    now: () => FETCHED_AT + 2 * 60_000,
    copyText: vi.fn(),
    setTimer: (fn, ms) => void setTimeout(fn, ms),
    ...overrides,
  }
}

function render(p: LpPayload, context = ctx()): HTMLElement {
  const card = buildLpCard(p, context)
  document.body.append(card)
  return card
}

function placeholder(src = '/api/v1/artifacts/lp-1'): HTMLElement {
  const node = document.createElement('div')
  node.className = 'msg-artifact-lp'
  node.dataset.lpSrc = src
  node.innerHTML = `<div class="msg-artifact-lp__body"></div>
    <p class="msg-artifact-lp__status">Loading liquidity…</p>`
  document.body.append(node)
  return node
}

async function flush(): Promise<void> {
  for (let i = 0; i < 5; i++) await Promise.resolve()
}

beforeEach(() => {
  document.body.replaceChildren()
})

afterEach(() => {
  vi.useRealTimers()
})

/* ── mime + normalization ──────────────────────────────────────────────── */

describe('isLpArtifact', () => {
  it('matches the lp mime, with or without parameters', () => {
    expect(isLpArtifact({ mime: LP_ARTIFACT_MIME })).toBe(true)
    expect(isLpArtifact({ mime: `${LP_ARTIFACT_MIME}; charset=utf-8` })).toBe(true)
    expect(isLpArtifact({ mime: 'APPLICATION/VND.AGENTOS.LP+JSON' })).toBe(true)
  })

  it('ignores every other artifact', () => {
    expect(isLpArtifact({ mime: 'application/json' })).toBe(false)
    expect(isLpArtifact({ mime: 'application/vnd.agentos.cards+json' })).toBe(false)
    expect(isLpArtifact(null)).toBe(false)
    expect(isLpArtifact({})).toBe(false)
  })
})

describe('normalizeLpPayload', () => {
  it.each(['pool', 'ranges', 'position', 'positions', 'positions-empty'])(
    'reads the %s fixture',
    (name) => {
      const p = normalizeLpPayload(fixture(name))
      expect(p).not.toBeNull()
      expect(p!.version).toBe(1)
      expect(p!.asOfBlock).toBe(21044901)
    },
  )

  it('returns null when there is nothing to draw', () => {
    expect(normalizeLpPayload(null)).toBeNull()
    expect(normalizeLpPayload([])).toBeNull()
    expect(normalizeLpPayload({ kind: 'swap' })).toBeNull()
    expect(normalizeLpPayload({ kind: 'pool' })).toBeNull()
    expect(normalizeLpPayload({ kind: 'position' })).toBeNull()
    expect(normalizeLpPayload({ kind: 'positions' })).toBeNull()
  })

  it('keeps an empty positions list: that is the empty-state card, not an error', () => {
    const p = normalizeLpPayload({ kind: 'positions', positions: [] })
    expect(p?.kind).toBe('positions')
  })

  it('keeps USD null as null rather than zero', () => {
    const p = payload<LpPositionPayload>('position')
    const raw = fixture('position') as { position: Record<string, unknown> }
    raw.position.valueUsd = null
    expect(p.position.valueUsd).toBe(1284.2)
    expect((normalizeLpPayload(raw) as LpPositionPayload).position.valueUsd).toBeNull()
  })

  it('does not invent a status it does not know', () => {
    const raw = fixture('position') as { position: Record<string, unknown> }
    raw.position.status = 'liquidated'
    expect((normalizeLpPayload(raw) as LpPositionPayload).position.status).toBe('unknown')
  })

  it('drops an explorer that is not http(s)', () => {
    expect(safeExplorerBase('javascript:alert(1)')).toBe('')
    expect(safeExplorerBase('https://basescan.org/')).toBe('https://basescan.org')
    const raw = fixture('position') as { position: { chain: Record<string, unknown> } }
    raw.position.chain.explorer = 'javascript:alert(1)'
    const p = normalizeLpPayload(raw) as LpPositionPayload
    expect(p.position.chain?.explorer).toBe('')
  })
})

/* ── formatting ─────────────────────────────────────────────────────────── */

describe('formatting', () => {
  it('compacts USD to three significant digits', () => {
    expect(formatUsdCompact(1284.2)).toBe('$1.28K')
    expect(formatUsdCompact(2_100_000)).toBe('$2.1M')
    expect(formatUsdCompact(11_400_000)).toBe('$11.4M')
    expect(formatUsdCompact(529_000_000)).toBe('$529M')
    expect(formatUsdCompact(999_950)).toBe('$1M')
    expect(formatUsdCompact(38.11)).toBe('$38.11')
  })

  it('never renders an unknown USD value as $0', () => {
    expect(formatUsdCompact(null)).toBe('—')
    expect(formatUsd(null)).toBe('—')
    expect(formatUsd(0)).toBe('$0.00')
  })

  it('keeps cents on a holding and significant digits on a sub-cent price', () => {
    expect(formatUsd(1284.2)).toBe('$1,284.20')
    expect(formatUsd(38.11)).toBe('$38.11')
    expect(formatUsd(412_000)).toBe('$412K')
    expect(formatUsd(0.00000921)).toBe('$0.0₅921')
  })

  it('formats token amounts with sensible significant digits', () => {
    expect(formatTokenAmount('1240000')).toBe('1,240,000')
    expect(formatTokenAmount('0.212')).toBe('0.212')
    expect(formatTokenAmount('12.3456')).toBe('12.35')
    expect(formatTokenAmount('22367000000')).toBe('22.4B')
    expect(formatTokenAmount('0')).toBe('0')
    expect(formatTokenAmount('0.00000000123')).toBe('0.0₈123')
    expect(formatTokenAmount('0.5085')).toBe('0.5085')
    expect(formatTokenAmount('not-a-number')).toBe('not-a-number')
  })

  it('counts leading zeros of a tiny price in a subscript', () => {
    expect(formatSmall(0.0000000019)).toBe('0.0₈19')
    expect(formatSmall(0.00012)).toBe('0.00012')
    expect(formatPrice(0.0000000154)).toBe('0.0₇154')
    expect(formatPrice(2450.5)).toBe('2,451')
  })

  it('prints percentages with one decimal', () => {
    expect(formatShare(0.42)).toBe('42.0%')
    expect(formatShare(0.0833)).toBe('8.3%')
    expect(formatSignedPct(16.3)).toBe('+16.3%')
    expect(formatSignedPct(-42.5)).toBe('−42.5%')
  })

  it('shortens addresses and says how long ago', () => {
    expect(shortAddress('0x7a3f9c21b4d5e6f708192a3b4c5d6e7f80919e3f')).toBe('0x7a3f…9e3f')
    expect(shortAddress('Main')).toBe('Main')
    const at = '2026-09-27T09:30:00Z'
    expect(relativeTime(at, FETCHED_AT + 10_000)).toBe('just now')
    expect(relativeTime(at, FETCHED_AT + 2 * 60_000)).toBe('2m ago')
    expect(relativeTime(at, FETCHED_AT + 3 * 3_600_000)).toBe('3h ago')
    expect(relativeTime(at, FETCHED_AT + 2 * 86_400_000)).toBe('2d ago')
    expect(relativeTime('garbage', FETCHED_AT)).toBe('')
  })

  it('builds explorer links only from a real address or hash', () => {
    const chain = { id: 8453, key: 'base', name: 'Base', explorer: 'https://basescan.org' }
    const address = '0x7a3f9c21b4d5e6f708192a3b4c5d6e7f80919e3f'
    expect(explorerUrl(chain, 'address', address)).toBe(`https://basescan.org/address/${address}`)
    const hash = `0x${'ab'.repeat(32)}`
    expect(explorerUrl(chain, 'tx', hash)).toBe(`https://basescan.org/tx/${hash}`)
    expect(explorerUrl(chain, 'address', 'javascript:alert(1)')).toBe('')
    expect(explorerUrl({ ...chain, explorer: '' }, 'address', address)).toBe('')
    expect(explorerUrl(null, 'address', address)).toBe('')
  })
})

/* ── geometry ───────────────────────────────────────────────────────────── */

describe('chart geometry', () => {
  it('uses market cap when every segment has one, price otherwise', () => {
    const ranges = payload<LpRangesPayload>('ranges')
    expect(chooseAxis(ranges.segments)).toBe('mcap')
    const priced = ranges.segments.map((s) => ({ ...s, mcapLower: null, mcapUpper: null }))
    expect(chooseAxis(priced)).toBe('price')
  })

  it('draws one bar per segment, tallest share at full height', () => {
    const model = buildChartModel(payload<LpRangesPayload>('ranges'))
    expect(model.axis).toBe('mcap')
    expect(model.bars).toHaveLength(5)
    expect(model.bars.map((b) => b.height)).toEqual([
      (0.1 / 0.42) * 100,
      (0.3 / 0.42) * 100,
      100,
      (0.08 / 0.42) * 100,
      (0.1 / 0.42) * 100,
    ])
    expect(model.bars[0]!.x).toBe(0)
    expect(model.bars[4]!.x + model.bars[4]!.width).toBeCloseTo(100)
    expect(model.ticks[0]).toEqual({ x: 0, label: '$310K' })
    expect(model.ticks.at(-1)).toEqual({ x: 100, label: '$529M' })
    expect(model.ticks.length).toBeLessThanOrEqual(5)
  })

  it('puts the current marker inside the active segment', () => {
    const model = buildChartModel(payload<LpRangesPayload>('ranges'))
    // Current mcap sits exactly on the active segment's lower bound.
    expect(model.marker).toEqual({ x: 40, label: 'now $3.87M', edge: null })
  })

  it('falls back to price and pins an off-chart current value to the edge', () => {
    const p = payload<LpRangesPayload>('ranges')
    const priced: LpRangesPayload = {
      ...p,
      segments: p.segments.map((s) => ({ ...s, mcapLower: null, active: false })),
      current: { ...p.current, priceUsd: 1 },
    }
    const model = buildChartModel(priced)
    expect(model.axis).toBe('price')
    expect(model.marker?.edge).toBe('right')
    expect(model.marker?.x).toBe(100)
  })

  it('places an in-range "now" proportionally inside the band, in log space', () => {
    const p = payload<LpPositionPayload>('position')
    const inside = positionMarker({
      ...p.position,
      status: 'in-range',
      pool: { ...p.position.pool, mcapUsd: Math.sqrt(2_100_000 * 9_800_000) },
    })
    expect(inside.x).toBeCloseTo((RANGE_BAND[0] + RANGE_BAND[1]) / 2)
    expect(inside.outside).toBeNull()
    expect(inside.pinned).toBe(false)
  })

  it('places an out-of-range "now" on the track past the band while it fits', () => {
    const p = payload<LpPositionPayload>('position')
    // $11.4M against a $2.1M–$9.8M range: past the upper edge, still on the track.
    const above = positionMarker(p.position)
    expect(above.outside).toBe('above')
    expect(above.pinned).toBe(false)
    expect(above.x).toBeGreaterThan(RANGE_BAND[1])
    expect(above.x).toBeLessThan(100)

    const below = positionMarker({
      ...p.position,
      status: 'below-range',
      pool: { ...p.position.pool, mcapUsd: 1_200_000 },
    })
    expect(below.outside).toBe('below')
    expect(below.pinned).toBe(false)
    expect(below.x).toBeGreaterThan(0)
    expect(below.x).toBeLessThan(RANGE_BAND[0])
  })

  it('pins a "now" beyond the track to its end and says so', () => {
    const p = payload<LpPositionPayload>('position')
    const below = positionMarker({
      ...p.position,
      status: 'below-range',
      pool: { ...p.position.pool, mcapUsd: 1 },
    })
    expect(below).toMatchObject({ x: 0, outside: 'below', pinned: true })
    const above = positionMarker({
      ...p.position,
      pool: { ...p.position.pool, mcapUsd: 1e12 },
    })
    expect(above).toMatchObject({ x: 100, outside: 'above', pinned: true })
  })

  it('lets the engine status win over geometry that disagrees', () => {
    const p = payload<LpPositionPayload>('position')
    // Status says above, but the price read puts it inside: still drawn past the band.
    const above = positionMarker({
      ...p.position,
      pool: { ...p.position.pool, mcapUsd: 5_000_000 },
    })
    expect(above.outside).toBe('above')
    expect(above.x).toBeGreaterThan(RANGE_BAND[1])
  })

  it('signs the out-of-range distance by side', () => {
    const p = payload<LpPositionPayload>('position').position
    expect(signedDistance(p)).toBe(16.3)
    expect(signedDistance({ ...p, status: 'below-range', distancePct: 72.6 })).toBe(-72.6)
    expect(signedDistance({ ...p, status: 'below-range', distancePct: -72.6 })).toBe(-72.6)
    expect(signedDistance({ ...p, status: 'in-range', distancePct: null })).toBeNull()
  })
})

/* ── DOM ────────────────────────────────────────────────────────────────── */

describe('position card', () => {
  it('draws the header, status, value, fees, range and footer', () => {
    const card = render(payload('position'))
    expect(card.dataset.lpKind).toBe('position')
    expect(card.dataset.lpStatus).toBe('above-range')
    expect(card.dataset.lpChain).toBe('base')
    expect(card.querySelector('.lp-card__pair')).toHaveTextContent('PEPE / WETH')
    expect(card.querySelector('.lp-card__meta')).toHaveTextContent('Base · 1%')
    // The status is a word, not only a colour.
    expect(card.querySelector('.lp-pill')).toHaveTextContent('Above range')
    expect(card.querySelector('[data-lp-hero="value"] .lp-hero__value')).toHaveTextContent(
      '$1,284.20',
    )
    expect(card.querySelector('[data-lp-hero="fees"] .lp-hero__value')).toHaveTextContent('+$38.11')
    expect(card.querySelector('.lp-range__axis')).toHaveTextContent('mcap')
    expect(card.querySelector('.lp-range__lower')).toHaveTextContent('$2.1M')
    expect(card.querySelector('.lp-range__upper')).toHaveTextContent('$9.8M')
    expect(card.querySelector('.lp-range__now')).toHaveTextContent(
      '▲ now $11.4M (+16.3%) above range',
    )
    expect(card.querySelector('[data-lp-row="principal"]')).toHaveTextContent(
      '0 PEPE · 0.5085 WETH',
    )
    expect(card.querySelector('[data-lp-row="fees"]')).toHaveTextContent('1,240,000 PEPE')
    expect(card.querySelector('.lp-card__foot-meta')).toHaveTextContent(
      '#48213 · as of block 21,044,901 · 2m ago',
    )
  })

  it('names the owner once, as the copy chip, never again in the footer text', () => {
    const card = render(payload('position'))
    const meta = card.querySelector('.lp-card__foot-meta')!.textContent ?? ''
    expect(meta).not.toContain('Main')
    expect(meta).not.toContain('0x7a3f')
    const foot = card.querySelector('.lp-card__foot')!.textContent ?? ''
    expect(foot.split('0x7a3f…9e3f')).toHaveLength(2)
    expect(foot.split('Main')).toHaveLength(2)
    const chip = card.querySelector('.lp-card__copy')!
    expect(chip.querySelector('.lp-card__action-label')).toHaveTextContent('owner')
    expect(chip.querySelector('.lp-card__action-name')).toHaveTextContent('Main')
    expect(chip.querySelector('.lp-card__action-value')).toHaveTextContent('0x7a3f…9e3f')
    // An unnamed owner is just the label and the address.
    const p = payload<LpPositionPayload>('position')
    const bare = render({
      ...p,
      position: { ...p.position, owner: { ...p.position.owner, label: null } },
    })
    expect(bare.querySelector('.lp-card__foot-meta')!.textContent).not.toContain('0x7a3f')
    expect(bare.querySelector('.lp-card__action-name')).toBeNull()
    expect(bare.querySelector('.lp-card__copy')).toHaveTextContent('owner0x7a3f…9e3f')
  })

  it('greens only unclaimed fees above zero: "$0.00" is muted, unpriced is "—"', () => {
    const p = payload<LpPositionPayload>('position')
    const hero = (usd: number | null) =>
      render({
        ...p,
        position: { ...p.position, fees: { ...p.position.fees, usd } },
      }).querySelector<HTMLElement>('[data-lp-hero="fees"] .lp-hero__value')!
    const earned = hero(38.11)
    expect(earned.textContent).toBe('+$38.11')
    expect(earned.dataset.lpFees).toBe('positive')
    const zero = hero(0)
    expect(zero.textContent).toBe('$0.00')
    expect(zero.dataset.lpFees).toBe('zero')
    expect(zero).not.toHaveAttribute('data-lp-no-price')
    const unpriced = hero(null)
    expect(unpriced.textContent).toBe('—')
    expect(unpriced.dataset.lpFees).toBe('none')
    expect(unpriced.title).toBe('No USD price is known for this value')
  })

  it('renders an unknown value as "—" with a no-price hint', () => {
    const p = payload<LpPositionPayload>('position')
    const card = render({
      ...p,
      position: { ...p.position, valueUsd: null, fees: { ...p.position.fees, usd: null } },
    })
    const value = card.querySelector<HTMLElement>('[data-lp-hero="value"] .lp-hero__value')!
    expect(value.textContent).toBe('—')
    expect(value.title).toBe('No USD price is known for this value')
    expect(card.querySelector('[data-lp-hero="fees"] .lp-hero__value')!.textContent).toBe('—')
    expect(card.textContent).not.toContain('$0')
  })

  it('shows the distance below the range with a minus sign', () => {
    const p = payload<LpPositionPayload>('position')
    const card = render({
      ...p,
      position: {
        ...p.position,
        status: 'below-range',
        distancePct: 42.5,
        pool: { ...p.position.pool, mcapUsd: 1_200_000 },
      },
    })
    expect(card.querySelector('.lp-range__now')).toHaveTextContent(
      '▲ now $1.2M (−42.5%) below range',
    )
    expect(card.querySelector('.lp-pill')).toHaveTextContent('Below range')
  })

  describe('range bar', () => {
    const left = (node: Element | null): number => parseFloat((node as HTMLElement).style.left)

    function bar(position: Partial<LpPosition>, mcap: number): HTMLElement {
      const p = payload<LpPositionPayload>('position')
      const card = render({
        ...p,
        position: {
          ...p.position,
          ...position,
          pool: { ...p.position.pool, mcapUsd: mcap },
          range: { ...p.position.range, mcapLower: 1_040_000, mcapUpper: 3_000_000 },
        },
      })
      return card.querySelector<HTMLElement>('.lp-range')!
    }

    it('draws the range as the filled band with its bounds on the band edges', () => {
      const range = bar({ status: 'in-range', distancePct: null }, 1_800_000)
      const band = range.querySelector<HTMLElement>('.lp-range__band')!
      const lower = range.querySelector('.lp-range__lower')!
      const upper = range.querySelector('.lp-range__upper')!
      expect(left(band)).toBe(RANGE_BAND[0])
      expect(parseFloat(band.style.width)).toBe(RANGE_BAND[1] - RANGE_BAND[0])
      // The bounds hang off the band's own edges, not the track's ends.
      expect(left(lower)).toBe(RANGE_BAND[0])
      expect(lower.getAttribute('data-align')).toBe('start')
      expect(left(upper)).toBe(RANGE_BAND[1])
      expect(upper.getAttribute('data-align')).toBe('end')
      expect(lower).toHaveTextContent('$1.04M')
      expect(upper).toHaveTextContent('$3M')
    })

    it('in range: "now" sits between the bounds', () => {
      const range = bar({ status: 'in-range', distancePct: null }, 1_800_000)
      const now = range.querySelector<HTMLElement>('.lp-range__now')!
      expect(range.dataset.lpNow).toBe('inside')
      expect(left(now)).toBeGreaterThan(left(range.querySelector('.lp-range__lower')))
      expect(left(now)).toBeLessThan(left(range.querySelector('.lp-range__upper')))
      expect(now).toHaveTextContent('▲ now $1.8M')
      expect(now.textContent).not.toMatch(/range/)
      expect(now.dataset.outside).toBeUndefined()
    })

    it('below range: "now" is pinned left of the lower bound and says "below range"', () => {
      // The tester's card: now $286K against a range starting at $1.04M.
      const range = bar({ status: 'below-range', distancePct: 72.6 }, 286_000)
      const now = range.querySelector<HTMLElement>('.lp-range__now')!
      const pin = range.querySelector<HTMLElement>('.lp-range__pin')!
      expect(range.dataset.lpNow).toBe('below')
      expect(range.dataset.lpPinned).toBe('true')
      expect(left(now)).toBe(0)
      expect(left(now)).toBeLessThan(left(range.querySelector('.lp-range__lower')))
      expect(now).toHaveTextContent('◀ now $286K (−72.6%) below range')
      expect(now.dataset.outside).toBe('below')
      expect(now.dataset.pinned).toBe('true')
      expect(now.dataset.align).toBe('start')
      expect(pin.dataset.pinned).toBe('below')
    })

    it('above range: "now" is pinned right of the upper bound and says "above range"', () => {
      const range = bar({ status: 'above-range', distancePct: 900 }, 40_000_000)
      const now = range.querySelector<HTMLElement>('.lp-range__now')!
      expect(range.dataset.lpNow).toBe('above')
      expect(left(now)).toBe(100)
      expect(left(now)).toBeGreaterThan(left(range.querySelector('.lp-range__upper')))
      expect(now).toHaveTextContent('now $40M (+900.0%) above range ▶')
      expect(now.dataset.align).toBe('end')
    })
  })

  it('renders symbols exactly as the payload gives them', () => {
    const p = payload<LpPositionPayload>('position')
    const card = render({
      ...p,
      position: { ...p.position, token: { ...p.position.token, symbol: 'boar' } },
    })
    expect(card.querySelector('.lp-card__base')!.textContent).toBe('boar')
    expect(card.querySelector('[data-lp-row="principal"]')).toHaveTextContent('0 boar')
  })

  it('links the owner on the chain explorer', () => {
    const card = render(payload('position'))
    const link = card.querySelector<HTMLAnchorElement>('.lp-card__explorer')!
    expect(link.href).toBe(
      'https://basescan.org/address/0x7a3f9c21b4d5e6f708192a3b4c5d6e7f80919e3f',
    )
    expect(link.rel).toBe('noopener noreferrer')
    expect(link.target).toBe('_blank')
  })

  it('copies the owner address it shows and briefly says so', async () => {
    vi.useFakeTimers()
    const copyText = vi.fn()
    const card = render(payload('position'), ctx({ copyText }))
    const button = card.querySelector<HTMLButtonElement>('.lp-card__copy')!
    expect(button).toHaveTextContent('ownerMain0x7a3f…9e3f')
    expect(button.querySelector('.lp-card__action-value')).toHaveTextContent('0x7a3f…9e3f')
    button.click()
    await flush()
    expect(copyText).toHaveBeenCalledWith('0x7a3f9c21b4d5e6f708192a3b4c5d6e7f80919e3f')
    expect(button.dataset.lpCopied).toBe('true')
    expect(button).toHaveTextContent('copied')
    vi.advanceTimersByTime(LP_COPIED_MS - 1)
    expect(button.dataset.lpCopied).toBe('true')
    vi.advanceTimersByTime(1)
    expect(button.dataset.lpCopied).toBeUndefined()
    expect(button.querySelector('.lp-card__action-value')).toHaveTextContent('0x7a3f…9e3f')
  })

  it('says "copy failed" when the clipboard refuses, never "copied"', async () => {
    vi.useFakeTimers()
    const card = render(
      payload('position'),
      ctx({ copyText: () => Promise.reject(new Error('denied')) }),
    )
    const button = card.querySelector<HTMLButtonElement>('.lp-card__copy')!
    button.click()
    await flush()
    expect(button.dataset.lpCopied).toBe('failed')
    expect(button).toHaveTextContent('copy failed')
    expect(button).not.toHaveTextContent('copied')
    vi.advanceTimersByTime(LP_COPIED_MS)
    expect(button.dataset.lpCopied).toBeUndefined()
  })

  it('never lets a payload string reach the DOM as markup', () => {
    const raw = fixture('position') as { position: { token: Record<string, unknown> } }
    raw.position.token.symbol = '<img src=x onerror=alert(1)>'
    const card = render(normalizeLpPayload(raw)!)
    expect(card.querySelector('img')).toBeNull()
    expect(card.querySelector('.lp-card__base')!.textContent).toBe('<img src=x onerror=alert(1)>')
  })
})

describe('ranges card', () => {
  it('draws a focusable SVG bar per segment and marks the active one', () => {
    const card = render(payload('ranges'))
    const bars = card.querySelectorAll<SVGElement>('.lp-chart__bar')
    expect(bars).toHaveLength(5)
    const tooltip = card.querySelector<HTMLElement>('[role="tooltip"]')!
    bars.forEach((bar) => {
      expect(bar.getAttribute('tabindex')).toBe('0')
      expect(bar.getAttribute('aria-describedby')).toBe(tooltip.id)
      expect(bar.getAttribute('aria-label')).toMatch(/of liquidity$/)
    })
    expect(card.querySelectorAll('[data-active="true"]')).toHaveLength(1)
    expect(bars[2]!.getAttribute('data-active')).toBe('true')
    expect(card.querySelector('.lp-chart__now')).toHaveTextContent('now $3.87M')
    expect(card.querySelector('.lp-chart__now-line')).not.toBeNull()
    expect(card.querySelector('[data-lp-badge="partial-scan"]')).toBeNull()
  })

  it('shows the segment range, share and both amounts on hover and focus', () => {
    const card = render(payload('ranges'))
    const bars = card.querySelectorAll<SVGElement>('.lp-chart__bar')
    const tooltip = card.querySelector<HTMLElement>('[role="tooltip"]')!
    expect(tooltip.hidden).toBe(true)

    bars[2]!.dispatchEvent(new FocusEvent('focus'))
    expect(tooltip.hidden).toBe(false)
    expect(tooltip.querySelector('[data-lp-line="range"]')).toHaveTextContent('$3.87M – $9.8M')
    expect(tooltip.querySelector('[data-lp-line="share"]')).toHaveTextContent(
      '42.0% of liquidity · active',
    )
    expect(tooltip.querySelector('[data-lp-line="amounts"]')).toHaveTextContent(
      '9.8B PEPE · 44.47 WETH',
    )
    expect(tooltip.querySelector('[data-lp-line="price"]')).toHaveTextContent('WETH per PEPE')

    bars[2]!.dispatchEvent(new FocusEvent('blur'))
    expect(tooltip.hidden).toBe(true)

    bars[0]!.dispatchEvent(new MouseEvent('mouseenter'))
    expect(tooltip.querySelector('[data-lp-line="share"]')).toHaveTextContent('10.0% of liquidity')
    bars[0]!.dispatchEvent(new MouseEvent('mouseleave'))
    expect(tooltip.hidden).toBe(true)
  })

  it('moves between bars with the arrow keys', () => {
    const card = render(payload('ranges'))
    const bars = card.querySelectorAll<SVGElement>('.lp-chart__bar')
    const focus = vi.spyOn(bars[1] as unknown as HTMLElement, 'focus')
    bars[0]!.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }))
    expect(focus).toHaveBeenCalled()
  })

  it('badges a truncated scan instead of drawing a silent chart', () => {
    const p = payload<LpRangesPayload>('ranges')
    const card = render({ ...p, scan: { ...p.scan, scannedWords: 12, truncated: true } })
    const badge = card.querySelector<HTMLElement>('[data-lp-badge="partial-scan"]')!
    expect(badge).toHaveTextContent('partial scan')
    expect(badge.title).toContain('12 of 40')
    expect(card.dataset.lpPartial).toBe('true')

    const flagged = render({ ...p, partialScan: true })
    expect(flagged.querySelector('[data-lp-badge="partial-scan"]')).not.toBeNull()
  })

  it('labels a price axis with its unit', () => {
    const p = payload<LpRangesPayload>('ranges')
    const card = render({
      ...p,
      segments: p.segments.map((s) => ({ ...s, mcapLower: null, mcapUpper: null })),
    })
    expect(card.querySelector('.lp-chart')!.getAttribute('data-lp-axis')).toBe('price')
    expect(card.querySelector('.lp-chart__caption')).toHaveTextContent('price (WETH per PEPE)')
  })
})

describe('pool card', () => {
  it('shows TVL, price, mcap, reserves, safety and top ranges', () => {
    const card = render(payload('pool'))
    expect(card.dataset.lpKind).toBe('pool')
    expect(card.querySelector('[data-lp-stat="tvl"]')).toHaveTextContent('TVL$412K')
    expect(card.querySelector('[data-lp-stat="mcap"]')).toHaveTextContent('$3.87M')
    expect(card.querySelector('[data-lp-stat="price"]')).toHaveTextContent('$0.0₅921')
    expect(card.querySelector('[data-lp-row="reserves"]')).toHaveTextContent(
      '22.4B PEPE ($206K) · 84.06 WETH ($206K)',
    )
    expect(card.querySelector('.lp-safety__launcher')).toHaveTextContent('Clanker')
    const lock = card.querySelector<HTMLElement>('.lp-safety__lock')!
    expect(lock).toHaveTextContent('LP locked')
    expect(lock.dataset.lpLocked).toBe('true')
    const rows = card.querySelectorAll('.lp-ranges__row')
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveTextContent('$2.1M – $9.8M')
    expect(rows[0]).toHaveTextContent('42.0%')
  })

  it('shows the token address it copies, and the pool id as its own chip', async () => {
    const copyText = vi.fn()
    const p = payload<LpPoolPayload>('pool')
    const card = render(p, ctx({ copyText }))
    const meta = card.querySelector('.lp-card__foot-meta')!
    // The pool id is no longer shown as if it were the copy target.
    expect(meta.textContent).not.toContain(p.pool.poolId.slice(0, 6))
    const chips = [...card.querySelectorAll<HTMLButtonElement>('.lp-card__copy')]
    expect(chips.map((c) => c.dataset.lpCopy)).toEqual(['token', 'pool'])
    const shown = chips.map((c) => [
      c.querySelector('.lp-card__action-label')!.textContent,
      c.querySelector('.lp-card__action-value')!.textContent,
    ])
    expect(shown).toEqual([
      ['token', shortAddress(p.token.address)],
      ['pool', shortAddress(p.pool.poolId)],
    ])
    chips[0]!.click()
    chips[1]!.click()
    await flush()
    expect(copyText.mock.calls).toEqual([[p.token.address], [p.pool.poolId]])
    const link = card.querySelector<HTMLAnchorElement>('.lp-card__explorer')!
    expect(link.href).toBe(`https://basescan.org/address/${p.token.address}`)
  })

  it('never changes the case of a symbol', () => {
    const p = payload<LpPoolPayload>('pool')
    const card = render({ ...p, token: { ...p.token, symbol: 'boar' } })
    expect(card.querySelector('.lp-card__base')!.textContent).toBe('boar')
    expect(card.querySelector('[data-lp-row="reserves"]')).toHaveTextContent('22.4B boar')
  })

  it('says when the lock is unknown or absent', () => {
    const p = payload('pool') as Extract<LpPayload, { kind: 'pool' }>
    const unknown = render({ ...p, safety: { ...p.safety, locked: null } })
    expect(unknown.querySelector('.lp-safety__lock')).toHaveTextContent('lock unknown')
    const open = render({ ...p, safety: { ...p.safety, locked: false } })
    expect(open.querySelector('.lp-safety__lock')).toHaveTextContent('not locked')
  })
})

describe('positions card', () => {
  it('keeps the engine order and flags unpriced rows', () => {
    const card = render(payload('positions'))
    const rows = [...card.querySelectorAll<HTMLElement>('.lp-row')]
    expect(rows.map((r) => r.dataset.lpTokenId)).toEqual(['48213', '1207', '48990'])
    expect(rows.map((r) => r.dataset.lpStatus)).toEqual(['above-range', 'below-range', 'in-range'])
    expect(rows[1]!.querySelector('.lp-row__value')).toHaveTextContent('— no price')
    expect(rows[1]!.querySelector('.lp-row__chain')).toHaveTextContent('Robinhood Chain')
    expect(rows[1]!.querySelector('.lp-row__wallet')).toHaveTextContent('0x1111…0000')
    expect(rows[0]!.querySelector('.lp-row__value')).toHaveTextContent('$1,284.20')
    // Out-of-range rows say how far out; the in-range row does not.
    expect(rows.map((r) => r.querySelector('.lp-row__distance')?.textContent ?? null)).toEqual([
      '+16.3%',
      '−42.5%',
      null,
    ])
    expect(rows[0]!.querySelector('.lp-row__status .lp-pill')).toHaveTextContent('Above range')
    // Unclaimed fees are a column of their own; unpriced reads "—".
    expect(rows.map((r) => r.querySelector('.lp-row__fees')!.textContent)).toEqual([
      '+$38.11',
      '—',
      '+$12.02',
    ])
    expect(card.querySelector('[data-lp-stat="count"]')).toHaveTextContent('3')
    expect(card.querySelector('[data-lp-stat="out-of-range"] .lp-stat__value')).toHaveAttribute(
      'data-lp-tone',
      'warn',
    )
    expect(card.querySelector('.lp-card__meta')).toHaveTextContent('2 wallets · 2 chains')
    // Two wallets: there is no single address to copy.
    expect(card.querySelector('.lp-card__copy')).toBeNull()
  })

  it('greens only row fees above zero: "$0.00" is muted, unpriced is "—" with the hint', () => {
    const p = payload<LpPositionsPayload>('positions')
    const fees = [12.5, 0, null]
    const card = render({
      ...p,
      positions: p.positions.map((row, i) => ({ ...row, fees: { ...row.fees, usd: fees[i]! } })),
    })
    const cells = [...card.querySelectorAll<HTMLElement>('.lp-row__fees')]
    expect(cells.map((c) => c.textContent)).toEqual(['+$12.50', '$0.00', '—'])
    expect(cells.map((c) => c.dataset.lpFees)).toEqual(['positive', 'zero', 'none'])
    expect(cells[1]).not.toHaveAttribute('data-lp-no-price')
    expect(cells[1]!.textContent).not.toContain('+')
    expect(cells[2]).toHaveAttribute('data-lp-no-price', 'true')
    expect(cells[2]!.title).toContain('No USD price is known for this value')
  })

  it('links each owner to its chain explorer, and drops the glyph when there is none', () => {
    const p = payload<LpPositionsPayload>('positions')
    const card = render(p)
    const owner = card.querySelector<HTMLAnchorElement>('.lp-row a.lp-row__wallet')!
    expect(owner.href).toBe(
      'https://basescan.org/address/0x7a3f9c21b4d5e6f708192a3b4c5d6e7f80919e3f',
    )
    expect(owner.target).toBe('_blank')
    expect(owner.rel).toBe('noopener noreferrer')
    expect(owner.dataset.lpLink).toBe('true')

    const bare = render({
      ...p,
      positions: p.positions.map((row) => ({
        ...row,
        chain: row.chain ? { ...row.chain, explorer: '' } : null,
      })),
    })
    expect(bare.querySelector('a.lp-row__wallet')).toBeNull()
    const cell = bare.querySelector<HTMLElement>('.lp-row__wallet')!
    expect(cell.tagName).toBe('SPAN')
    expect(cell.dataset.lpLink).toBeUndefined()
  })

  it('names every chain block in a multi-chain footer', () => {
    const card = render(payload('positions'))
    expect(card.querySelector('.lp-card__foot-meta')).toHaveTextContent(
      'as of Base #21,044,901 · Robinhood Chain #9,120,331 · 2m ago',
    )
  })

  it('names a single wallet once, as the copy chip, not in the footer text', () => {
    const p = payload<LpPositionsPayload>('positions')
    const single = p.wallets[0]!
    const card = render({ ...p, wallets: [single] })
    const meta = card.querySelector('.lp-card__foot-meta')!.textContent ?? ''
    expect(meta).not.toContain('Main')
    expect(meta).not.toContain('0x7a3f')
    const chip = card.querySelector('.lp-card__copy')!
    expect(chip).toHaveTextContent('walletMain0x7a3f…9e3f')
    expect(
      (card.querySelector('.lp-card__foot')!.textContent ?? '').split('0x7a3f…9e3f'),
    ).toHaveLength(2)
  })

  it('keeps the single-block footer when only one chain was read', () => {
    const p = payload<LpPositionsPayload>('positions')
    const card = render({ ...p, asOfBlocks: [{ key: 'base', block: 21044901 }] })
    expect(card.querySelector('.lp-card__foot-meta')).toHaveTextContent(
      'as of block 21,044,901 · 2m ago',
    )
  })

  it('renders the empty state naming the wallets and chains', () => {
    const card = render(payload('positions-empty'))
    expect(card.dataset.lpEmpty).toBe('true')
    expect(card.querySelector('.lp-card__empty')).toHaveTextContent(
      'No Uniswap V4 positions in Main on Base, Robinhood Chain',
    )
    expect(card.querySelector('.lp-rows')).toBeNull()
  })
})

/* ── mounter ────────────────────────────────────────────────────────────── */

describe('createLpMounter', () => {
  it('fetches the payload, renders the card and clears the status', async () => {
    const host = placeholder()
    const fetchPayload = vi.fn().mockResolvedValue(fixture('position'))
    const mounter = createLpMounter({ fetchPayload, now: () => FETCHED_AT })
    mounter.mountLp(document.body)
    await flush()
    expect(fetchPayload).toHaveBeenCalledWith('/api/v1/artifacts/lp-1')
    expect(host.querySelector('.lp-card')).not.toBeNull()
    // The kind is stamped once, on the card; the host only says it holds one.
    expect(host.querySelectorAll('[data-lp-kind]')).toHaveLength(1)
    expect(host.querySelector('.lp-card')!.getAttribute('data-lp-kind')).toBe('position')
    expect(host.dataset.lpKind).toBeUndefined()
    expect(host.dataset.lpHost).toBe('rendered')
    expect(host.querySelector<HTMLElement>('.msg-artifact-lp__status')!.hidden).toBe(true)
    mounter.destroyAll()
  })

  it('claims each host once, so repeated mounts do not double-render', async () => {
    placeholder()
    const fetchPayload = vi.fn().mockResolvedValue(fixture('pool'))
    const mounter = createLpMounter({ fetchPayload })
    mounter.mountLp(document.body)
    mounter.mountLp(document.body)
    await flush()
    mounter.mountLp(document.body)
    expect(fetchPayload).toHaveBeenCalledTimes(1)
    expect(document.querySelectorAll('.lp-card')).toHaveLength(1)
    mounter.destroyAll()
  })

  it('reports an unreadable payload and a failed fetch without throwing', async () => {
    const bad = placeholder('/a')
    const broken = placeholder('/b')
    const fetchPayload = vi.fn((url: string) =>
      url === '/a' ? Promise.resolve({ kind: 'nope' }) : Promise.reject(new Error('HTTP 500')),
    )
    const mounter = createLpMounter({ fetchPayload })
    mounter.mountLp(document.body)
    await flush()
    expect(bad.querySelector('.msg-artifact-lp__status')).toHaveTextContent(
      'Liquidity data could not be read.',
    )
    expect(broken.querySelector('.msg-artifact-lp__status')).toHaveTextContent(
      'Liquidity card failed to load.',
    )
  })

  it('says so when the placeholder carries an empty source', async () => {
    const host = placeholder('')
    const fetchPayload = vi.fn()
    createLpMounter({ fetchPayload }).mountLp(document.body)
    await flush()
    expect(fetchPayload).not.toHaveBeenCalled()
    expect(host.querySelector('.msg-artifact-lp__status')).toHaveTextContent(
      'Liquidity data is unavailable.',
    )
  })

  it('does not render into a host that left the document mid-fetch', async () => {
    const host = placeholder()
    let resolve: (value: unknown) => void = () => {}
    const fetchPayload = vi.fn(() => new Promise((r) => (resolve = r)))
    const mounter = createLpMounter({ fetchPayload })
    mounter.mountLp(document.body)
    host.remove()
    resolve(fixture('position'))
    await flush()
    expect(host.querySelector('.lp-card')).toBeNull()
  })

  it('refreshes the relative time once a minute and stops on destroyAll', async () => {
    vi.useFakeTimers()
    let now = FETCHED_AT
    const host = placeholder()
    const mounter = createLpMounter({
      fetchPayload: () => Promise.resolve(fixture('position')),
      now: () => now,
    })
    mounter.mountLp(document.body)
    await flush()
    const ago = host.querySelector('.lp-card__ago')!
    expect(ago).toHaveTextContent('just now')

    now += 3 * 60_000
    vi.advanceTimersByTime(LP_CLOCK_MS)
    expect(ago).toHaveTextContent('3m ago')

    expect(vi.getTimerCount()).toBe(1)
    mounter.destroyAll()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('stops the clock once every card has left the document', async () => {
    vi.useFakeTimers()
    const host = placeholder()
    const mounter = createLpMounter({ fetchPayload: () => Promise.resolve(fixture('ranges')) })
    mounter.mountLp(document.body)
    await flush()
    expect(vi.getTimerCount()).toBe(1)
    host.remove()
    vi.advanceTimersByTime(LP_CLOCK_MS)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('copies through the Clipboard API by default and shows "copied" once it resolves', async () => {
    vi.useFakeTimers()
    const writeText = vi.fn(() => Promise.resolve())
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } })
    try {
      const host = placeholder()
      const mounter = createLpMounter({ fetchPayload: () => Promise.resolve(fixture('position')) })
      mounter.mountLp(document.body)
      await flush()
      const button = host.querySelector<HTMLButtonElement>('.lp-card__copy')!
      button.click()
      await flush()
      expect(writeText).toHaveBeenCalledWith('0x7a3f9c21b4d5e6f708192a3b4c5d6e7f80919e3f')
      expect(button.dataset.lpCopied).toBe('true')
      expect(button).toHaveTextContent('copied')
      vi.advanceTimersByTime(LP_COPIED_MS)
      expect(button.dataset.lpCopied).toBeUndefined()
      mounter.destroyAll()
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('falls back to execCommand when the Clipboard API refuses', async () => {
    const writeText = vi.fn(() => Promise.reject(new Error('Document is not focused.')))
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } })
    const exec = vi.fn(() => true)
    const original = document.execCommand
    document.execCommand = exec as unknown as typeof document.execCommand
    try {
      const host = placeholder()
      const mounter = createLpMounter({ fetchPayload: () => Promise.resolve(fixture('position')) })
      mounter.mountLp(document.body)
      await flush()
      const button = host.querySelector<HTMLButtonElement>('.lp-card__copy')!
      button.click()
      await flush()
      expect(exec).toHaveBeenCalledWith('copy')
      expect(button.dataset.lpCopied).toBe('true')
      expect(document.querySelector('textarea')).toBeNull()

      exec.mockReturnValue(false)
      button.click()
      await flush()
      expect(button.dataset.lpCopied).toBe('failed')
      expect(button).toHaveTextContent('copy failed')
      mounter.destroyAll()
    } finally {
      document.execCommand = original
      vi.unstubAllGlobals()
    }
  })

  it('clears a pending "copied" reset on destroyAll', async () => {
    vi.useFakeTimers()
    const host = placeholder()
    const mounter = createLpMounter({
      fetchPayload: () => Promise.resolve(fixture('position')),
      copyText: () => {},
    })
    mounter.mountLp(document.body)
    await flush()
    host.querySelector<HTMLButtonElement>('.lp-card__copy')!.click()
    await flush()
    expect(vi.getTimerCount()).toBe(2)
    mounter.destroyAll()
    expect(vi.getTimerCount()).toBe(0)
  })
})

/* ── engine fixtures (written by the Python side) ──────────────────────── */

const engineFixtures = existsSync(ENGINE_FIXTURES)
  ? readdirSync(ENGINE_FIXTURES).filter((name) => name.endsWith('.json'))
  : []

describe.skipIf(engineFixtures.length === 0)('engine fixtures', () => {
  it.each(engineFixtures)('%s normalizes and renders', (name) => {
    const raw = JSON.parse(readFileSync(`${ENGINE_FIXTURES}/${name}`, 'utf8')) as {
      kind?: string
    }
    const p = normalizeLpPayload(raw)
    expect(p, name).not.toBeNull()
    expect(p!.kind).toBe(raw.kind)
    const card = render(p!)
    expect(card.dataset.lpKind).toBe(raw.kind)
    expect(card.querySelector('.lp-card__foot')).not.toBeNull()
    // A multi-chain read names every chain's block in the footer.
    const blocks = Object.values((raw as { asOfBlocks?: Record<string, number> }).asOfBlocks ?? {})
    if (blocks.length > 1) {
      const meta = card.querySelector('.lp-card__foot-meta')!.textContent ?? ''
      for (const block of blocks) expect(meta).toContain(`#${block.toLocaleString('en-US')}`)
    }
    // Symbols render exactly as the engine sent them.
    const symbol = (raw as { token?: { symbol?: string } }).token?.symbol
    if (symbol) expect(card.querySelector('.lp-card__base')!.textContent).toBe(symbol)
  })
})

/* ── measured layout ────────────────────────────────────────────────────── */

// jsdom lays nothing out, so these stub the widths the browser would measure.
function stubWidth(node: Element | null, prop: 'offsetWidth' | 'clientWidth', px: number): void {
  if (!node) throw new Error(`no node to stub ${prop} on`)
  Object.defineProperty(node, prop, { configurable: true, value: px })
}

describe('lpLayoutFor', () => {
  it('folds position rows onto one line only from LP_DENSE_MIN_PX', () => {
    expect(LP_DENSE_MIN_PX).toBe(760)
    // The tester's desktop card (608) and the floor that must not clip (560).
    expect(lpLayoutFor(560)).toBe('stacked')
    expect(lpLayoutFor(608)).toBe('stacked')
    expect(lpLayoutFor(759)).toBe('stacked')
    expect(lpLayoutFor(760)).toBe('dense')
    expect(lpLayoutFor(1024)).toBe('dense')
  })

  it('goes to three lines per row under LP_NARROW_MAX_PX', () => {
    expect(LP_NARROW_MAX_PX).toBe(440)
    // The desk with its Book panel open measures ~360.
    expect(lpLayoutFor(360)).toBe('narrow')
    expect(lpLayoutFor(439)).toBe('narrow')
    expect(lpLayoutFor(440)).toBe('stacked')
    expect(lpLayoutFor(1)).toBe('narrow')
  })
})

describe('fitLabel', () => {
  it('centres a label that fits, else hangs it toward the room', () => {
    expect(fitLabel(100, 80, 400)).toEqual({ align: 'center', fits: true })
    expect(fitLabel(10, 80, 400)).toEqual({ align: 'start', fits: true })
    expect(fitLabel(395, 80, 400)).toEqual({ align: 'end', fits: true })
  })

  it('says when no alignment keeps it inside, and picks the least overflow', () => {
    expect(fitLabel(0, 500, 400)).toEqual({ align: 'start', fits: false })
    expect(fitLabel(400, 500, 400)).toEqual({ align: 'end', fits: false })
    expect(fitLabel(30, 90, 100).fits).toBe(false)
  })
})

describe('layoutRangeLabels', () => {
  // A 560px desktop card: 27px of padding, "MCAP" and its gap leave ~487px of
  // track. Label widths are the desk's 10.5px mono (~6.3px a glyph).
  const at560 = { trackPx: 487, lowerPx: 44, upperPx: 38, nowPx: 208, nowWrapPx: 126 }

  it('keeps one label line each at 560px, pinned or not', () => {
    expect(layoutRangeLabels({ ...at560, x: 100 })).toEqual({
      bounds: 'inline',
      nowAlign: 'end',
      nowWrap: false,
    })
    expect(layoutRangeLabels({ ...at560, x: 0 })).toEqual({
      bounds: 'inline',
      nowAlign: 'start',
      nowWrap: false,
    })
    expect(layoutRangeLabels({ ...at560, x: 50 }).nowAlign).toBe('center')
  })

  it('lifts the upper bound a line when the band is too narrow for both', () => {
    // 200px track: the band is 100px and the bounds need 70 + 8 + 70.
    const narrow = layoutRangeLabels({
      trackPx: 200,
      x: 50,
      lowerPx: 70,
      upperPx: 70,
      nowPx: 90,
      nowWrapPx: 60,
    })
    expect(narrow.bounds).toBe('stacked')
    expect(narrow.nowWrap).toBe(false)
  })

  it('stacks the distance and side under the reading when the track is too short', () => {
    const narrow = layoutRangeLabels({
      trackPx: 180,
      x: 100,
      lowerPx: 40,
      upperPx: 34,
      nowPx: 208,
      nowWrapPx: 126,
    })
    expect(narrow).toEqual({ bounds: 'inline', nowAlign: 'end', nowWrap: true })
    // A centred pin with a wrapped reading that fits centred stays centred.
    expect(
      layoutRangeLabels({
        trackPx: 180,
        x: 50,
        lowerPx: 40,
        upperPx: 34,
        nowPx: 208,
        nowWrapPx: 126,
      }),
    ).toEqual({ bounds: 'inline', nowAlign: 'center', nowWrap: true })
  })
})

describe('visibleTicks', () => {
  it('keeps every tick when they clear each other', () => {
    const ticks = [0, 25, 50, 75, 100].map((x) => ({ x, width: 50 }))
    expect(visibleTicks(ticks, 530)).toEqual([true, true, true, true, true])
  })

  it('drops inner ticks that would touch a neighbour, never the ends', () => {
    const ticks = [0, 25, 50, 75, 100].map((x) => ({ x, width: 60 }))
    // 300px: [0,60] [45,105] [120,180] [195,255] [240,300].
    expect(visibleTicks(ticks, 300)).toEqual([true, false, true, false, true])
    expect(visibleTicks([], 300)).toEqual([])
    expect(visibleTicks([{ x: 0, width: 40 }], 300)).toEqual([true])
  })
})

describe('layoutLpCard', () => {
  it('stamps the row layout from the measured card width', () => {
    const card = render(payload('positions'))
    layoutLpCard(card)
    // Unmeasured (0): left as rendered, which the CSS draws stacked.
    expect(card.dataset.lpLayout).toBeUndefined()
    stubWidth(card, 'offsetWidth', 608)
    layoutLpCard(card)
    expect(card.dataset.lpLayout).toBe('stacked')
    stubWidth(card, 'offsetWidth', 820)
    layoutLpCard(card)
    expect(card.dataset.lpLayout).toBe('dense')
    stubWidth(card, 'offsetWidth', 560)
    layoutLpCard(card)
    expect(card.dataset.lpLayout).toBe('stacked')
    stubWidth(card, 'offsetWidth', 439)
    layoutLpCard(card)
    expect(card.dataset.lpLayout).toBe('narrow')
    stubWidth(card, 'offsetWidth', 440)
    layoutLpCard(card)
    expect(card.dataset.lpLayout).toBe('stacked')
    stubWidth(card, 'offsetWidth', 360)
    layoutLpCard(card)
    expect(card.dataset.lpLayout).toBe('narrow')
  })

  it('splits the "now" reading so it can stack, and stacks it on a narrow track', () => {
    const p = payload<LpPositionPayload>('position')
    const card = render(p)
    const range = card.querySelector<HTMLElement>('.lp-range')!
    const now = range.querySelector<HTMLElement>('.lp-range__now')!
    expect(now.querySelector('.lp-range__now-value')).toHaveTextContent('▲ now $11.4M')
    expect(now.querySelector('.lp-range__now-side')).toHaveTextContent('(+16.3%) above range')

    stubWidth(card, 'offsetWidth', 560)
    stubWidth(range.querySelector('.lp-range__track'), 'clientWidth', 487)
    stubWidth(range.querySelector('.lp-range__lower'), 'offsetWidth', 44)
    stubWidth(range.querySelector('.lp-range__upper'), 'offsetWidth', 38)
    stubWidth(now, 'offsetWidth', 208)
    stubWidth(now.querySelector('.lp-range__now-value'), 'offsetWidth', 80)
    stubWidth(now.querySelector('.lp-range__now-side'), 'offsetWidth', 126)
    layoutLpCard(card)
    expect(range.dataset.lpNowWrap).toBeUndefined()
    expect(range.dataset.lpBounds).toBeUndefined()

    // Narrow: the reading no longer fits the track, and the bounds meet.
    stubWidth(range.querySelector('.lp-range__track'), 'clientWidth', 150)
    stubWidth(range.querySelector('.lp-range__lower'), 'offsetWidth', 60)
    stubWidth(range.querySelector('.lp-range__upper'), 'offsetWidth', 60)
    layoutLpCard(card)
    expect(range.dataset.lpNowWrap).toBe('true')
    expect(range.dataset.lpBounds).toBe('stacked')
    expect(['start', 'center', 'end']).toContain(now.dataset.align)

    // Wide again: both go back.
    stubWidth(range.querySelector('.lp-range__track'), 'clientWidth', 487)
    stubWidth(range.querySelector('.lp-range__lower'), 'offsetWidth', 44)
    stubWidth(range.querySelector('.lp-range__upper'), 'offsetWidth', 38)
    layoutLpCard(card)
    expect(range.dataset.lpNowWrap).toBeUndefined()
    expect(range.dataset.lpBounds).toBeUndefined()
  })

  it('hides chart ticks that would touch on a narrow plot', () => {
    const card = render(payload('ranges'))
    const ticks = [...card.querySelectorAll<HTMLElement>('.lp-chart__tick')]
    expect(ticks.length).toBeGreaterThan(2)
    stubWidth(card, 'offsetWidth', 560)
    ticks.forEach((tick) => stubWidth(tick, 'offsetWidth', 50))
    stubWidth(card.querySelector('.lp-chart__plot'), 'clientWidth', 530)
    layoutLpCard(card)
    expect(ticks.filter((tick) => tick.dataset.lpHidden)).toHaveLength(0)

    stubWidth(card.querySelector('.lp-chart__plot'), 'clientWidth', 120)
    layoutLpCard(card)
    expect(ticks[0]!.dataset.lpHidden).toBeUndefined()
    expect(ticks[ticks.length - 1]!.dataset.lpHidden).toBeUndefined()
    expect(ticks.filter((tick) => tick.dataset.lpHidden).length).toBeGreaterThan(0)
  })
})

describe('createLpMounter layout', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('re-fits each card when it resizes, on the next frame, and stops on destroyAll', async () => {
    let fire: (entries: Array<{ target: Element }>) => void = () => {}
    const observed: Element[] = []
    const disconnect = vi.fn()
    vi.stubGlobal(
      'ResizeObserver',
      class {
        constructor(cb: (entries: Array<{ target: Element }>) => void) {
          fire = cb
        }
        observe(node: Element): void {
          observed.push(node)
        }
        unobserve(): void {}
        disconnect = disconnect
      },
    )
    const frames: Array<() => void> = []
    vi.stubGlobal('requestAnimationFrame', (fn: () => void) => frames.push(fn))
    vi.stubGlobal('cancelAnimationFrame', () => {})

    const host = placeholder()
    const mounter = createLpMounter({ fetchPayload: () => Promise.resolve(fixture('positions')) })
    mounter.mountLp(document.body)
    await flush()
    const card = host.querySelector<HTMLElement>('.lp-card')!
    expect(observed).toEqual([card])

    stubWidth(card, 'offsetWidth', 800)
    fire([{ target: card }])
    fire([{ target: card }])
    // Never inside the observer callback; one frame however many entries.
    expect(card.dataset.lpLayout).toBeUndefined()
    expect(frames).toHaveLength(1)
    frames.shift()!()
    expect(card.dataset.lpLayout).toBe('dense')

    stubWidth(card, 'offsetWidth', 608)
    fire([{ target: card }])
    frames.shift()!()
    expect(card.dataset.lpLayout).toBe('stacked')

    // The desk's Book panel opening squeezes the card under LP_NARROW_MAX_PX.
    stubWidth(card, 'offsetWidth', 360)
    fire([{ target: card }])
    frames.shift()!()
    expect(card.dataset.lpLayout).toBe('narrow')

    mounter.destroyAll()
    expect(disconnect).toHaveBeenCalled()
  })
})
