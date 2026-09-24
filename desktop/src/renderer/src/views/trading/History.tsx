import { ChainBadge } from './ChainMark'
import {
  ArrowDownLeft,
  ArrowLeftRight,
  ArrowUpRight,
  ExternalLink,
  Flame,
  History as HistoryIcon,
  PackageOpen,
  ShieldCheck,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useCallback, useState } from 'react'
import { useRpc } from '@/app/providers'
import { Button } from '~/components/ui/button'
import { t } from '~/i18n'
import { desktopApi } from '~/lib/desktop-api'
import {
  errorText,
  formatAmount,
  formatAmountCompact,
  formatUsd,
  groupEntriesByDay,
  initiatorKey,
  shortAddress,
  formatUsdCell,
} from './logic'
import { Empty, ErrorState, Skeleton, Sym } from './parts'
import type { Entry, EntryKind } from './types'

const GLYPH: Record<EntryKind, LucideIcon> = {
  swap: ArrowLeftRight,
  deposit: ArrowDownLeft,
  withdraw: ArrowUpRight,
  approval: ShieldCheck,
  gas: Flame,
  unwrap: PackageOpen,
}

const timeFmt = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit' })
const dayFmt = new Intl.DateTimeFormat(undefined, {
  weekday: 'short',
  day: 'numeric',
  month: 'short',
})

function dayLabel(day: string, now: number): string {
  const d = new Date(`${day}T00:00:00`)
  const today = new Date(now)
  const yesterday = new Date(now - 86_400_000)
  const same = (a: Date, b: Date) =>
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  if (same(d, today)) return t('trading.history.today')
  if (same(d, yesterday)) return t('trading.history.yesterday')
  return dayFmt.format(d)
}

/**
 * The ledger as a list: grouped by day, one glyph per kind, the two legs of
 * a swap stacked, who started it, and a door to the explorer.
 */
export function History({
  entries,
  loading,
  now,
  showWallet,
  error,
  onRetry,
  nextBefore = null,
  wallet,
  chainId,
}: {
  entries: Entry[]
  loading: boolean
  now: number
  showWallet: boolean
  /** The first page failed: shown instead of "no activity". */
  error?: unknown
  onRetry?: () => void
  /** The engine's cursor for the page after `entries`; null when there is none. */
  nextBefore?: number | null
  /** The same filter the first page was read with, so the next pages match. */
  wallet?: string
  chainId?: number
}) {
  const more = useMoreHistory(wallet, chainId)
  if (error && entries.length === 0) {
    return <ErrorState error={error} onRetry={onRetry ?? (() => {})} />
  }
  if (loading && entries.length === 0) {
    return (
      <div>
        {[0, 1, 2].map((i) => (
          <div key={i} className="trd-entry">
            <span className="trd-entry__glyph" />
            <span className="trd-entry__what">
              <Skeleton width={110} />
            </span>
            <span className="trd-entry__legs">
              <Skeleton width={90} />
            </span>
            <span className="trd-entry__value">
              <Skeleton width={60} />
            </span>
            <span className="trd-entry__who">
              <Skeleton width={40} />
            </span>
            <span className="trd-entry__link" />
          </div>
        ))}
      </div>
    )
  }
  if (entries.length === 0) {
    return (
      <Empty
        icon={<HistoryIcon className="size-8" strokeWidth={1.25} aria-hidden />}
        title={t('trading.history.empty')}
        body={t('trading.history.empty.body')}
      />
    )
  }
  // The first page refetches on its own clock; the pages after it are pinned
  // until the filter changes. An entry that moved between pages shows once.
  const seen = new Set(entries.map((e) => e.id))
  const all = [...entries, ...more.entries.filter((e) => !seen.has(e.id))]
  const cursor = more.nextBefore === undefined ? nextBefore : more.nextBefore
  return (
    <div aria-label={t('trading.tab.history')}>
      {groupEntriesByDay(all).map((group) => (
        <div key={group.day}>
          <div className="trd-day">{dayLabel(group.day, now)}</div>
          {group.entries.map((e) => (
            <EntryRow key={e.id} entry={e} showWallet={showWallet} />
          ))}
        </div>
      ))}
      {cursor !== null ? (
        <div className="trd-history__more">
          {more.error ? <span className="trd-send__error">{errorText(more.error)}</span> : null}
          <Button
            variant="secondary"
            disabled={more.loading}
            onClick={() => void more.load(cursor)}
            data-testid="history-more"
          >
            {more.loading ? t('trading.history.loading') : t('trading.history.more')}
          </Button>
        </div>
      ) : null}
    </div>
  )
}

/**
 * The pages after the first. `useHistory` reads one page with no cursor, so
 * the rest are read here with `before`, and dropped when the filter moves.
 */
interface MorePages {
  key: string
  entries: Entry[]
  /** undefined = not paged yet: the first page's own cursor still applies. */
  nextBefore: number | null | undefined
  loading: boolean
  error: unknown
}

const NO_PAGES: MorePages = {
  key: '',
  entries: [],
  nextBefore: undefined,
  loading: false,
  error: null,
}

function useMoreHistory(wallet: string | undefined, chainId: number | undefined) {
  const rpc = useRpc()
  const [state, setState] = useState<MorePages>(NO_PAGES)
  const key = `${wallet ?? 'all'}:${chainId ?? 'all'}`
  const live = state.key === key ? state : NO_PAGES
  const load = useCallback(
    async (before: number) => {
      setState({ ...live, key, loading: true, error: null })
      try {
        await rpc.waitForConnection()
        const page = await rpc.call<{ entries?: Entry[]; nextBefore?: number | null }>(
          'trading.history',
          {
            ...(wallet ? { wallet } : {}),
            ...(chainId ? { chainId } : {}),
            before,
            limit: 100,
          },
        )
        setState({
          key,
          entries: [...live.entries, ...(page.entries ?? [])],
          nextBefore: page.nextBefore ?? null,
          loading: false,
          error: null,
        })
      } catch (err) {
        setState({ ...live, key, loading: false, error: err })
      }
    },
    [rpc, wallet, chainId, key, live],
  )
  return { ...live, load }
}

/**
 * An approval entry whose amount is zero set an allowance to nothing: a
 * revoke. The ledger keeps the kind; the row says what it was.
 */
export function isRevokeEntry(entry: Pick<Entry, 'kind' | 'amountIn'>): boolean {
  return entry.kind === 'approval' && entry.amountIn !== null && Number(entry.amountIn) === 0
}

function EntryRow({ entry, showWallet }: { entry: Entry; showWallet: boolean }) {
  const Glyph = GLYPH[entry.kind]
  const by = initiatorKey(entry.initiator)
  const revoke = isRevokeEntry(entry)
  // The chain leads the sub-line as a mark, so the rest stays plain text. A
  // revoke reads as a plain "Revoke": the ledger entry carries no spender
  // field, and its note is free text the agent wrote (`--note`), so naming
  // a spender from it would let a note claim "revoked Permit2" for a revoke
  // of anything. The spender lives in the Allowances tab, read from chain.
  const sub = [showWallet ? shortAddress(entry.wallet) : null, revoke ? null : entry.note]
    .filter(Boolean)
    .join(' · ')
  return (
    <div
      className="trd-entry"
      data-testid="history-entry"
      data-kind={entry.kind}
      data-revoke={revoke || undefined}
    >
      <span className="trd-entry__glyph" data-kind={entry.kind} aria-hidden>
        <Glyph className="size-3.5" strokeWidth={1.75} />
      </span>
      <span className="trd-entry__what">
        <span className="trd-entry__kind">
          {revoke ? t('trading.history.kind.revoke') : t(`trading.history.kind.${entry.kind}`)}
        </span>
        <span className="trd-entry__sub">
          <ChainBadge chainId={entry.chainId} />
          {sub ? ` · ${sub}` : ''}
        </span>
      </span>
      <span className="trd-entry__legs">
        {revoke && entry.tokenIn ? (
          // Nothing left the wallet: the token alone, no "−0".
          <span className="trd-entry__out">
            <Sym symbol={entry.tokenIn.symbol} />
          </span>
        ) : entry.tokenIn && entry.amountIn ? (
          <span className="trd-entry__out" title={formatAmount(entry.amountIn, 18)}>
            −{formatAmountCompact(entry.amountIn)} <Sym symbol={entry.tokenIn.symbol} />
          </span>
        ) : null}
        {entry.tokenOut && entry.amountOut ? (
          <span className="trd-entry__in" title={formatAmount(entry.amountOut, 18)}>
            +{formatAmountCompact(entry.amountOut)} <Sym symbol={entry.tokenOut.symbol} />
          </span>
        ) : null}
      </span>
      <span className="trd-entry__value" title={formatUsd(entry.valueUsd)}>
        {formatUsdCell(entry.valueUsd)}
        {entry.gasUsd !== null && entry.gasUsd > 0 ? (
          <small title={formatUsd(entry.gasUsd)}>
            {formatUsdCell(entry.gasUsd)} {t('trading.history.gas')}
          </small>
        ) : null}
      </span>
      <span className="trd-entry__who">
        <span className="trd-by" data-by={by}>
          {t(`trading.history.by.${by}`)}
        </span>
        <span className="trd-entry__time block">{timeFmt.format(new Date(entry.ts))}</span>
      </span>
      <span className="trd-entry__link">
        {entry.explorerUrl ? (
          <Button
            variant="ghost"
            size="icon"
            aria-label={t('trading.history.explorer')}
            title={t('trading.history.explorer')}
            onClick={() => void desktopApi().app.openExternal(entry.explorerUrl as string)}
          >
            <ExternalLink
              className="size-3.5 text-muted-foreground"
              strokeWidth={1.75}
              aria-hidden
            />
          </Button>
        ) : null}
      </span>
    </div>
  )
}
