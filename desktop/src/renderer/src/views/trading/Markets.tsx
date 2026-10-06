import {
  ArrowLeftRight,
  ExternalLink,
  Layers,
  RotateCw,
  Search,
  ShieldAlert,
  TriangleAlert,
} from 'lucide-react'
import { useState } from 'react'
import { Button } from '~/components/ui/button'
import { Switch } from '~/components/ui/switch'
import { t } from '~/i18n'
import { desktopApi } from '~/lib/desktop-api'
import { shortAge } from '~/lib/relative-time'
import { useNow } from '~/lib/use-now'
import { useMarkets, useTokenSearch } from '~/stores/trading'
import { useTradingUi } from '~/stores/trading-ui'
import { ChainBadge, ChainMark } from './ChainMark'
import { formatPct, formatPrice, pnlTone } from './logic'
import {
  ADDRESS_RE,
  dexLabel,
  feeLabel,
  formatPoolPrice,
  formatRatio,
  formatUsdShort,
  MIN_TVL_STEPS,
  pairParts,
  pickMarketsTarget,
  poolAge,
  poolSwapTokens,
  ROBINHOOD_CHAIN,
  SECTION_CAP,
  tvlStepLabel,
  type MarketsTarget,
} from './markets-logic'
import { ErrorState, Skeleton, Spinner, Sym, TokenLogo } from './parts'
import { QuoteWarnings } from './SwapPanel'
import {
  CHAINS,
  type ChainId,
  type MarketsPayload,
  type MarketsPool,
  type MarketsSide,
  type MarketsToken,
  type Token,
} from './types'

function fill(text: string, vars: Record<string, string | number>): string {
  return Object.entries(vars).reduce((s, [k, v]) => s.split(`{${k}}`).join(String(v)), text)
}

/**
 * Markets: every pool one token trades in, on every DEX of the chain
 * (docs/markets.md). Two lists — the tokens priced in it, then what it is
 * priced in — each row with its venue, depth, flow, price and age, and a
 * Swap that fills the ticket. The search and filters live in the trading-ui
 * store, so a trip to the ticket and back finds the list where it was.
 */
export function Markets({ deskChain = null }: { deskChain?: ChainId | null }) {
  const view = useTradingUi((s) => s.markets)
  const setView = useTradingUi((s) => s.setMarkets)
  const requestSwap = useTradingUi((s) => s.requestSwap)
  const now = useNow(60_000)

  // The field edits a draft; Return commits it. A Holdings row's Markets
  // action commits from outside, and the draft follows.
  const [draft, setDraft] = useState(view.query)
  const [shownQuery, setShownQuery] = useState(view.query)
  if (shownQuery !== view.query) {
    setShownQuery(view.query)
    setDraft(view.query)
  }

  const committed = view.query.trim()
  const explicit = view.chainId !== null
  const chain = view.chainId ?? deskChain ?? ROBINHOOD_CHAIN
  const needsLookup = committed.length > 0 && !view.address && !ADDRESS_RE.test(committed)
  const search = useTokenSearch(chain, needsLookup ? committed : '')
  const settled =
    !needsLookup || committed.length < 2 || (search.debounced === committed && !search.isFetching)
  const resolved: MarketsTarget | null = !committed
    ? null
    : view.address
      ? { chainId: chain, target: view.address, token: null }
      : settled
        ? pickMarketsTarget(committed, search.tokens, chain, explicit)
        : null

  const markets = useMarkets(
    {
      target: resolved?.target ?? '',
      chainId: resolved?.chainId ?? chain,
      minTvlUsd: view.minTvlUsd,
      lookalikes: view.lookalikes,
      deep: view.deep,
    },
    resolved !== null,
  )
  const data = markets.data
  const shownChain = resolved?.chainId ?? chain

  function submit() {
    const query = draft.trim()
    if (query === view.query && !view.address) {
      if (resolved) void markets.refetch()
      return
    }
    setView({ query, address: null })
  }

  return (
    <div className="trd-mk" data-testid="markets">
      <form
        className="trd-mk__bar"
        role="search"
        onSubmit={(e) => {
          e.preventDefault()
          submit()
        }}
      >
        <label className="trd-picker__search trd-mk__search">
          <Search className="size-3.5 text-dim" strokeWidth={2} aria-hidden />
          <input
            value={draft}
            placeholder={t('trading.markets.search')}
            spellCheck={false}
            autoComplete="off"
            aria-label={t('trading.markets.search.label')}
            data-testid="markets-search"
            onChange={(e) => setDraft(e.target.value)}
          />
          {(needsLookup && !settled) || markets.isFetching ? <Spinner /> : null}
        </label>
        <div
          role="radiogroup"
          aria-label={t('trading.markets.chain')}
          className="mac-segmented trd-mk__chains"
        >
          {[...CHAINS]
            .sort((a, b) => Number(b.id === ROBINHOOD_CHAIN) - Number(a.id === ROBINHOOD_CHAIN))
            .map((c) => (
              <button
                key={c.id}
                type="button"
                role="radio"
                aria-checked={shownChain === c.id}
                aria-label={c.name}
                title={c.name}
                className="mac-segment app-no-drag"
                data-testid={`markets-chain-${c.id}`}
                onClick={() => setView({ chainId: c.id, address: null })}
              >
                <ChainMark chainId={c.id} />
                <span className="trd-mk__chainword">{c.short}</span>
              </button>
            ))}
        </div>
      </form>

      <div className="trd-mk__filters">
        <span className="trd-mk__label">{t('trading.markets.minTvl')}</span>
        <div
          role="radiogroup"
          aria-label={t('trading.markets.minTvl')}
          className="mac-segmented trd-mk__tvl-steps"
        >
          {MIN_TVL_STEPS.map((usd) => (
            <button
              key={usd}
              type="button"
              role="radio"
              aria-checked={view.minTvlUsd === usd}
              className="mac-segment app-no-drag"
              data-testid={`markets-tvl-${usd}`}
              onClick={() => setView({ minTvlUsd: usd })}
            >
              {tvlStepLabel(usd)}
            </button>
          ))}
        </div>
        <label className="trd-mk__toggle" title={t('trading.markets.lookalikes.help')}>
          <Switch
            checked={view.lookalikes}
            onCheckedChange={(on) => setView({ lookalikes: on })}
            aria-label={t('trading.markets.lookalikes')}
            data-testid="markets-lookalikes"
          />
          <span>{t('trading.markets.lookalikes')}</span>
        </label>
        <button
          type="button"
          className="trd-chip app-no-drag"
          aria-pressed={view.deep}
          title={t('trading.markets.deep.help')}
          data-testid="markets-deep"
          onClick={() => setView({ deep: !view.deep })}
        >
          <Layers className="size-3" strokeWidth={2} aria-hidden />
          <span className="trd-chip__label">{t('trading.markets.deep')}</span>
        </button>
        <Button
          variant="ghost"
          size="icon"
          className="trd-mk__refresh"
          aria-label={t('trading.markets.refresh')}
          title={t('trading.markets.refresh')}
          disabled={!resolved || markets.isFetching}
          data-testid="markets-refresh"
          onClick={() => void markets.refetch()}
        >
          <RotateCw
            className="size-3.5 text-muted-foreground"
            strokeWidth={1.75}
            data-spinning={markets.isFetching || undefined}
            aria-hidden
          />
        </Button>
      </div>

      {!committed ? (
        <div className="trd-empty" data-testid="markets-prompt">
          <Layers className="size-8" strokeWidth={1.25} aria-hidden />
          <b>{t('trading.markets.prompt.title')}</b>
          <p>{t('trading.markets.prompt.body')}</p>
        </div>
      ) : !resolved ? (
        <div className="trd-mk__wait" data-testid="markets-resolving" aria-busy="true">
          <Spinner />
          <span>{fill(t('trading.markets.resolving'), { query: committed })}</span>
        </div>
      ) : markets.isError && !data ? (
        <div data-testid="markets-error">
          <ErrorState error={markets.error} onRetry={() => void markets.refetch()} />
        </div>
      ) : !data ? (
        <MarketsSkeleton />
      ) : (
        <Board
          data={data}
          now={now}
          minTvlUsd={view.minTvlUsd}
          refreshing={markets.isFetching}
          onSwap={(pool) => requestSwap(poolSwapTokens(data, pool))}
        />
      )}
    </div>
  )
}

function MarketsSkeleton() {
  return (
    <div className="trd-mk__skeleton" data-testid="markets-loading" aria-busy="true">
      <span className="sr-only">{t('trading.markets.loading')}</span>
      {[0, 1, 2, 3, 4].map((i) => (
        <div key={i} className="trd-mk__skelrow">
          <Skeleton width={i % 2 ? 96 : 120} />
          <Skeleton width={56} />
          <Skeleton width={44} />
        </div>
      ))}
    </div>
  )
}

function Board({
  data,
  now,
  minTvlUsd,
  refreshing,
  onSwap,
}: {
  data: MarketsPayload
  now: number
  minTvlUsd: number
  refreshing: boolean
  onSwap: (pool: MarketsPool) => void
}) {
  const tvl = tvlStepLabel(data.request?.params?.minTvlUsd ?? minTvlUsd)
  const nothing = data.sections.quote.length === 0 && data.sections.base.length === 0
  const c = data.counts
  return (
    <div className="trd-mk__board" data-refreshing={refreshing || undefined}>
      <TokenHead data={data} now={now} />
      {nothing ? (
        <div className="trd-empty" data-testid="markets-empty">
          <Layers className="size-8" strokeWidth={1.25} aria-hidden />
          <b>{fill(t('trading.markets.empty.title'), { tvl })}</b>
          <p>{t('trading.markets.empty.body')}</p>
        </div>
      ) : (
        (['quote', 'base'] as const).map((side) => (
          <Section
            key={side}
            side={side}
            rows={data.sections[side]}
            data={data}
            tvl={tvl}
            now={now}
            onSwap={onSwap}
          />
        ))
      )}
      <footer className="trd-mk__foot" data-testid="markets-foot">
        <span className="trd-mk__counts trd-num">
          {[
            fill(t('trading.markets.counts.shown'), { shown: c.shown, scanned: c.scanned }),
            c.belowMinTvl > 0
              ? fill(t('trading.markets.counts.below'), { count: c.belowMinTvl, tvl })
              : null,
            c.hiddenLookalikes > 0
              ? fill(t('trading.markets.counts.lookalikes'), { count: c.hiddenLookalikes })
              : null,
          ]
            .filter(Boolean)
            .join(' · ')}
        </span>
        {data.partial ? (
          <span
            className="mac-chip trd-mk__partial"
            data-tone="warn"
            title={t('trading.markets.partial.help')}
            data-testid="markets-partial"
          >
            <TriangleAlert className="size-2.5" strokeWidth={2.25} aria-hidden />
            {t('trading.markets.partial')}
          </span>
        ) : null}
      </footer>
      <QuoteWarnings warnings={data.warnings} />
    </div>
  )
}

function TokenHead({ data, now }: { data: MarketsPayload; now: number }) {
  const tk = data.token
  const oracle = tk.oracle
  const logo: Token = {
    chainId: data.chain.id,
    address: tk.address,
    symbol: tk.symbol,
    name: tk.name,
    decimals: tk.decimals,
    logoUrl: tk.logoUrl,
    native: false,
    verified: tk.verified,
  }
  const oracleTone = oracle?.paused ? 'danger' : oracle?.stale ? 'warn' : null
  return (
    <header className="trd-mk__head" data-testid="markets-head">
      <TokenLogo token={logo} size={30} />
      <div className="trd-mk__id">
        <span className="trd-mk__sym">
          <Sym symbol={tk.symbol} />
          <ChainBadge chainId={data.chain.id} className="trd-asset__chain" />
          {tk.stockToken ? (
            <span className="mac-chip" data-tone="ok">
              {t('trading.markets.stock')}
            </span>
          ) : null}
        </span>
        <span className="trd-mk__name" title={tk.name}>
          {tk.name}
        </span>
      </div>
      <div className="trd-mk__quote">
        <b className="trd-num" title={t('trading.markets.price')}>
          {formatPrice(tk.priceUsd)}
        </b>
        {oracle ? (
          <span
            className="trd-mk__oracle"
            title={fill(t('trading.markets.oracle.help'), {
              age: shortAge(now - oracle.ageSeconds * 1000, now),
            })}
            data-testid="markets-oracle"
          >
            <span>{t('trading.markets.oracle')}</span>
            <span className="trd-num">{formatPrice(oracle.usd)}</span>
            {oracleTone ? (
              <span className="mac-chip" data-tone={oracleTone} data-testid="markets-oracle-badge">
                {oracle.paused
                  ? t('trading.markets.oracle.paused')
                  : t('trading.markets.oracle.stale')}
              </span>
            ) : null}
          </span>
        ) : null}
      </div>
    </header>
  )
}

function Section({
  side,
  rows,
  data,
  tvl,
  now,
  onSwap,
}: {
  side: MarketsSide
  rows: MarketsPool[]
  data: MarketsPayload
  tvl: string
  now: number
  onSwap: (pool: MarketsPool) => void
}) {
  const [open, setOpen] = useState(false)
  const symbol = data.token.symbol
  const shown = open ? rows : rows.slice(0, SECTION_CAP)
  const more = rows.length - shown.length
  return (
    <section className="trd-mk__section" data-side={side} data-testid={`markets-section-${side}`}>
      <h3
        className="trd-mk__title"
        title={fill(t(`trading.markets.section.${side}.help`), { symbol })}
      >
        <span>{fill(t(`trading.markets.section.${side}`), { symbol })}</span>
        <span className="trd-mk__count trd-num">{rows.length}</span>
      </h3>
      {rows.length === 0 ? (
        <p className="trd-mk__none">{fill(t('trading.markets.section.empty'), { symbol, tvl })}</p>
      ) : (
        <>
          <div className="trd-mk__cols" aria-hidden>
            <span>{t('trading.markets.col.pair')}</span>
            <span>{t('trading.markets.col.venue')}</span>
            <span>{t('trading.markets.col.tvl')}</span>
            <span>{t('trading.markets.col.volume')}</span>
            <span>{t('trading.markets.col.price')}</span>
            <span>{t('trading.markets.col.change')}</span>
            <span>{t('trading.markets.col.age')}</span>
            <span />
          </div>
          <ul className="trd-mk__rows">
            {shown.map((pool) => (
              <Row
                key={`${pool.side}:${pool.poolAddress}`}
                pool={pool}
                token={data.token}
                now={now}
                onSwap={onSwap}
              />
            ))}
          </ul>
          {more > 0 ? (
            <button
              type="button"
              className="trd-mk__more app-no-drag"
              data-testid={`markets-more-${side}`}
              onClick={() => setOpen(true)}
            >
              {fill(t('trading.markets.more'), { count: more })}
            </button>
          ) : null}
        </>
      )}
    </section>
  )
}

function Row({
  pool,
  token,
  now,
  onSwap,
}: {
  pool: MarketsPool
  token: MarketsToken
  now: number
  onSwap: (pool: MarketsPool) => void
}) {
  const [first, second] = pairParts(pool.pair)
  const cp = pool.counterparty
  const fee = feeLabel(pool.feePct)
  const logo: Token = {
    chainId: pool.swap.chainId,
    address: cp.address,
    symbol: cp.symbol,
    name: cp.name,
    decimals: cp.decimals,
    logoUrl: cp.logoUrl,
    native: false,
    verified: cp.verified,
  }
  const symbol = token.symbol
  const legs = poolSwapTokens({ token }, pool)
  const swapLabel = fill(t('trading.markets.swap.label'), {
    from: legs.tokenIn.symbol,
    to: legs.tokenOut.symbol,
  })
  // A launchpad's own DEX (Bankr on Bankr) needs no second word for it: the
  // venue itself wears the launcher's tint.
  const launchedHere = pool.launcher !== null && pool.launcher === pool.dex.label
  const launchedTitle = pool.launcher
    ? fill(t('trading.markets.launcher'), { name: pool.launcher })
    : undefined
  return (
    <li
      className="trd-mk__row"
      data-side={pool.side}
      data-lookalike={cp.lookalike || undefined}
      data-testid="markets-row"
    >
      <span className="trd-mk__pair" title={cp.name}>
        <TokenLogo token={logo} size={18} />
        <span className="trd-mk__pairtext">
          <Sym symbol={first} className="trd-mk__first" />
          <span className="trd-mk__slash">/</span>
          <Sym symbol={second} className="trd-mk__second" />
        </span>
        {cp.lookalike ? (
          <span
            className="mac-chip trd-mk__flag"
            data-tone="warn"
            data-kind="lookalike"
            title={t('trading.markets.flag.lookalike.help')}
          >
            <ShieldAlert className="size-2.5" strokeWidth={2.25} aria-hidden />
            {t('trading.markets.flag.lookalike')}
          </span>
        ) : null}
      </span>
      <span className="trd-mk__venue">
        <span
          className="trd-mk__dex"
          data-launcher={launchedHere || undefined}
          title={launchedHere ? launchedTitle : undefined}
        >
          {dexLabel({ ...pool.dex, version: null })}
        </span>
        {pool.dex.version ? <span className="trd-mk__ver">{pool.dex.version}</span> : null}
        {fee ? (
          <span className="trd-mk__fee trd-num" title={fill(t('trading.markets.fee'), { fee })}>
            {fee}
          </span>
        ) : null}
        {pool.launcher && !launchedHere ? (
          <span className="mac-chip trd-mk__launcher" title={launchedTitle}>
            {pool.launcher}
          </span>
        ) : null}
        {pool.viaUniswap ? (
          <span
            className="mac-chip trd-mk__flag"
            data-tone="primary"
            data-kind="uni"
            title={t('trading.markets.flag.uni.help')}
          >
            {t('trading.markets.flag.uni')}
          </span>
        ) : null}
        {cp.stockToken ? (
          <span
            className="mac-chip trd-mk__flag"
            data-tone="ok"
            data-kind="stock"
            title={t('trading.markets.flag.stock.help')}
          >
            {t('trading.markets.flag.stock')}
          </span>
        ) : null}
      </span>
      <span className="trd-mk__tvl trd-num" title={t('trading.markets.tvl')}>
        {formatUsdShort(pool.tvlUsd)}
      </span>
      <span
        className="trd-mk__vol trd-num"
        title={
          pool.txns24h
            ? fill(t('trading.markets.txns'), {
                buys: pool.txns24h.buys,
                sells: pool.txns24h.sells,
              })
            : t('trading.markets.volume')
        }
      >
        {formatUsdShort(pool.volume24hUsd)}
      </span>
      <span className="trd-mk__px trd-num">
        <span>{formatPoolPrice(pool.priceUsd)}</span>
        <small>
          {formatRatio(pool.priceInToken)} {pool.side === 'quote' ? symbol : second || cp.symbol}
        </small>
      </span>
      <span
        className="trd-mk__chg trd-num"
        data-tone={pnlTone(pool.change24hPct)}
        title={t('trading.markets.change')}
      >
        {formatPct(pool.change24hPct, { signed: true })}
        {pool.premiumPct !== null && pool.side === 'base' ? (
          <small
            className="trd-mk__premium"
            data-tone={pnlTone(pool.premiumPct)}
            data-testid="markets-premium"
          >
            {fill(t('trading.markets.premium'), {
              pct: formatPct(pool.premiumPct, { signed: true }),
            })}
          </small>
        ) : null}
      </span>
      <span className="trd-mk__age trd-num" title={t('trading.markets.age')}>
        {poolAge(pool.createdAt, now)}
      </span>
      <span className="trd-mk__act">
        <Button
          variant="ghost"
          size="icon"
          aria-label={t('trading.markets.open')}
          title={t('trading.markets.open')}
          className="trd-mk__open"
          onClick={() => void desktopApi().app.openExternal(pool.url)}
        >
          <ExternalLink className="size-3 text-muted-foreground" strokeWidth={1.75} aria-hidden />
        </Button>
        <Button
          size="md"
          className="trd-mk__swap"
          aria-label={swapLabel}
          title={t('trading.markets.swap.help')}
          data-testid="markets-swap"
          onClick={() => onSwap(pool)}
        >
          <ArrowLeftRight className="size-3" strokeWidth={2} aria-hidden />
          <span>{t('trading.markets.swap')}</span>
        </Button>
      </span>
    </li>
  )
}
