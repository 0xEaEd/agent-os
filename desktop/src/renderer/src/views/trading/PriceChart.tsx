import { X } from 'lucide-react'
import {
  AreaSeries,
  ColorType,
  createChart,
  type IChartApi,
  type TickMarkType,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts'
import { useEffect, useRef, useState } from 'react'
import { Button } from '~/components/ui/button'
import { t } from '~/i18n'
import { useChart } from '~/stores/trading'
import { formatPct, formatPrice, formatUsd } from './logic'
import { ErrorState, Spinner } from './parts'
import type { Chart, ChartRange, Holding } from './types'

const RANGES: ChartRange[] = ['1h', '6h', '1d', '1w', 'all']

/** A tick label on the machine's own clock: a time inside a day, a date across
 *  days. `t` is unix seconds, the units both chart producers emit. */
function clock(time: Time, withTime: boolean): string {
  const at = new Date(Number(time) * 1000)
  return withTime
    ? at.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
    : at.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '#888'
}

/**
 * The picked holding's price under the table: one line, never candles.
 *
 * A line needs a close per point and nothing else, which is also the cheapest
 * thing to fetch — the short ranges are served from the snapshots the sync
 * loop already writes, without a request. The strip above it reads its four
 * figures out of the price response the line's own lookup made, so it too is
 * free. When the line came from snapshots rather than an indexed pool, it
 * says so.
 */
export function PriceChart({ holding, onClose }: { holding: Holding; onClose: () => void }) {
  const [range, setRange] = useState<ChartRange>('1d')
  const query = useChart(holding.chainId, holding.token.address, range)
  const stats = query.data?.stats
  const price = stats?.priceUsd ?? holding.priceUsd
  const change = stats?.changePct ?? null
  const tone = change === null ? 'flat' : change > 0 ? 'up' : change < 0 ? 'down' : 'flat'
  // A token priced against itself is a tautology, not a figure.
  const quote =
    stats?.quoteSymbol && stats.quoteSymbol.toUpperCase() !== holding.token.symbol.toUpperCase()
      ? stats.quoteSymbol
      : null

  return (
    <section className="trd-chart" aria-label={t('trading.chart.title')}>
      <dl className="trd-chart__strip">
        <Stat label={t('trading.chart.price')} value={formatPrice(price)} />
        <Stat
          label={t('trading.chart.marketCap')}
          value={formatUsd(stats?.marketCapUsd ?? null, { compact: true })}
        />
        <Stat
          label={`${t('trading.chart.priceIn')} ${quote ?? t('trading.chart.quote')}`}
          value={
            stats?.priceNative != null && quote
              ? `${stats.priceNative.toPrecision(4)} ${quote}`
              : '—'
          }
        />
        <Stat label={t('trading.chart.market')} value={stats?.market ?? '—'} mono={false} />
      </dl>

      <div className="trd-chart__head">
        <div className="trd-chart__hero">
          <b className="trd-chart__value trd-num">{formatPrice(price)}</b>
          <div className="trd-chart__move" data-tone={tone}>
            <span className="trd-num">{formatPct(change, { signed: true })}</span>
            <small>{t(`trading.chart.range.${range}`)}</small>
            {query.data?.source === 'snapshots' ? (
              <small title={t('trading.chart.snapshots')}>{t('trading.chart.local')}</small>
            ) : null}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <div role="radiogroup" aria-label={t('trading.chart.title')} className="mac-segmented">
            {RANGES.map((r) => (
              <button
                key={r}
                type="button"
                role="radio"
                aria-checked={range === r}
                className="mac-segment app-no-drag"
                onClick={() => setRange(r)}
              >
                {t(`trading.chart.range.${r}`)}
              </button>
            ))}
          </div>
          <Button
            variant="ghost"
            size="icon"
            aria-label={t('trading.chart.close')}
            onClick={onClose}
          >
            <X className="size-4 text-muted-foreground" strokeWidth={1.75} aria-hidden />
          </Button>
        </div>
      </div>

      {query.isError ? (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      ) : query.isPending ? (
        <div className="trd-chart__empty">
          <Spinner />
        </div>
      ) : !query.data || query.data.points.length < 2 ? (
        <div className="trd-chart__empty">{t('trading.chart.empty')}</div>
      ) : (
        <Canvas chart={query.data} />
      )}
    </section>
  )
}

function Stat({ label, value, mono = true }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="trd-chart__stat">
      <dt>{label}</dt>
      <dd className={mono ? 'trd-num' : undefined}>{value}</dd>
    </div>
  )
}

function Canvas({ chart }: { chart: Chart }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const fg = cssVar('--muted-foreground')
    const primary = cssVar('--primary')
    const hairline = cssVar('--hairline')
    let api: IChartApi
    try {
      api = createChart(el, {
        autoSize: true,
        layout: {
          background: { type: ColorType.Solid, color: 'transparent' },
          textColor: fg,
          fontFamily: getComputedStyle(el).fontFamily,
          fontSize: 10,
          attributionLogo: false,
        },
        // Only the horizontals: the value is what is being read, and vertical
        // rules chop a line this thin into segments.
        grid: { vertLines: { visible: false }, horzLines: { color: hairline, style: 2 } },
        rightPriceScale: { borderVisible: false },
        // The library labels in UTC by default, which reads as the wrong hour
        // to anyone not on it. Both the axis and the crosshair use the
        // machine's own clock instead.
        localization: { timeFormatter: (time: Time) => clock(time, true) },
        timeScale: {
          borderVisible: false,
          timeVisible: true,
          secondsVisible: false,
          tickMarkFormatter: (time: Time, type: TickMarkType) => clock(time, type >= 3),
        },
        crosshair: { mode: 0 },
        handleScroll: false,
        handleScale: false,
      })
    } catch {
      // jsdom and headless renderers have no canvas; the chart is decoration there.
      return
    }
    const series = api.addSeries(AreaSeries, {
      lineColor: primary,
      lineWidth: 2,
      topColor: `color-mix(in srgb, ${primary} 30%, transparent)`,
      bottomColor: 'transparent',
      priceLineVisible: false,
    })
    series.setData(
      [...chart.points]
        .sort((a, b) => a.t - b.t)
        // `t` is unix seconds, which is what the library wants: no divide.
        .map((p) => ({ time: Math.floor(p.t) as UTCTimestamp, value: p.c })),
    )
    api.timeScale().fitContent()
    return () => api.remove()
  }, [chart])
  return <div ref={ref} className="trd-chart__canvas" data-testid="price-chart" />
}
