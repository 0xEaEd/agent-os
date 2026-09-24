import { ExternalLink, RefreshCw, ShieldCheck, ShieldOff } from 'lucide-react'
import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { Button } from '~/components/ui/button'
import { t } from '~/i18n'
import { desktopApi } from '~/lib/desktop-api'
import { useAllowances, useRevoke } from '~/stores/trading'
import { errorText, formatAmount, formatUsd, shortAddress } from './logic'
import { AssetCell, Empty, ErrorState, Spinner } from './parts'
import type { Allowance } from './types'

const ARM_RESET_MS = 4000

/**
 * What contracts may spend from a wallet, read live. Every swap the desk
 * makes leaves an allowance behind — this is where they are seen and cut.
 * Unlimited ones lead; "at stake" is what the spender could take today,
 * which is the number that decides whether to bother.
 */
export function Allowances({ wallet }: { wallet: string | undefined }) {
  const list = useAllowances(wallet)
  const revoke = useRevoke()
  const [armed, setArmed] = useState<string | null>(null)

  useEffect(() => {
    if (!armed) return
    const id = window.setTimeout(() => setArmed(null), ARM_RESET_MS)
    return () => window.clearTimeout(id)
  }, [armed])

  function keyOf(a: Allowance): string {
    return `${a.chainId}:${a.token.address}:${a.spender}`.toLowerCase()
  }

  function onRevoke(a: Allowance) {
    const key = keyOf(a)
    if (armed !== key) {
      setArmed(key)
      return
    }
    setArmed(null)
    revoke.mutate(
      {
        chainId: a.chainId,
        wallet: a.wallet,
        token: a.token.address,
        spender: a.spender,
      },
      {
        onSuccess: () =>
          toast.success(t('trading.allowances.revoked'), { id: `trd-revoke-${key}` }),
        onError: (err) =>
          toast.error(`${t('trading.allowances.revokeFailed')}: ${errorText(err)}`, {
            id: `trd-revoke-${key}`,
          }),
      },
    )
  }

  const rows = list.allowances
  return (
    <section className="trd-allow" aria-label={t('trading.allowances.title')}>
      <header className="trd-allow__head">
        <p className="trd-allow__lead">{t('trading.allowances.lead')}</p>
        <Button
          variant="ghost"
          size="icon"
          aria-label={t('trading.allowances.refresh')}
          title={t('trading.allowances.refresh')}
          disabled={list.isFetching}
          onClick={() => void list.refetch()}
          data-testid="allowances-refresh"
        >
          {list.isFetching ? (
            <Spinner className="size-3.5" />
          ) : (
            <RefreshCw className="size-3.5 text-muted-foreground" strokeWidth={1.75} aria-hidden />
          )}
        </Button>
      </header>
      {list.scanning ? (
        <p className="trd-allow__scanning" data-testid="allowances-scanning">
          <Spinner className="size-3" />
          {t('trading.allowances.scanning')}
        </p>
      ) : null}
      {list.unlimitedCount > 0 ? (
        <p className="trd-allow__warn" data-testid="allowances-warn">
          <ShieldOff className="size-3.5" strokeWidth={2} aria-hidden />
          {list.unlimitedCount}{' '}
          {list.unlimitedCount === 1
            ? t('trading.allowances.warn')
            : t('trading.allowances.warn.plural')}
        </p>
      ) : null}
      {list.isError && rows.length === 0 ? (
        <ErrorState error={list.error} onRetry={() => void list.refetch()} />
      ) : list.isPending && rows.length === 0 ? (
        <div className="trd-allow__loading">
          <Spinner className="size-4" />
        </div>
      ) : rows.length === 0 ? (
        <Empty
          icon={<ShieldCheck className="size-8" strokeWidth={1.25} aria-hidden />}
          title={t('trading.allowances.empty')}
          body={t('trading.allowances.empty.body')}
        />
      ) : (
        <ul className="trd-allow__list">
          {rows.map((a) => {
            const key = keyOf(a)
            // The same spender can hold allowances on several tokens and
            // chains; only the row being revoked is the busy one.
            const v = revoke.variables
            const busy =
              revoke.isPending &&
              v !== undefined &&
              `${v.chainId}:${v.token}:${v.spender}`.toLowerCase() === key
            return (
              <li
                key={key}
                className="trd-allow__row"
                data-unlimited={a.unlimited || undefined}
                data-testid="allowance-row"
              >
                <AssetCell token={a.token} showChain />
                <div className="trd-allow__spender">
                  <b>{a.spenderLabel ?? t('trading.allowances.unknownSpender')}</b>
                  {a.spenderUrl ? (
                    <button
                      type="button"
                      className="trd-allow__addr trd-mono app-no-drag"
                      title={a.spender}
                      onClick={() => void desktopApi().app.openExternal(a.spenderUrl as string)}
                    >
                      {shortAddress(a.spender)}
                      <ExternalLink className="size-3" strokeWidth={1.75} aria-hidden />
                    </button>
                  ) : (
                    <span className="trd-allow__addr trd-mono" title={a.spender}>
                      {shortAddress(a.spender)}
                    </span>
                  )}
                </div>
                <div className="trd-allow__amount trd-num">
                  <b data-tone={a.unlimited ? 'danger' : undefined}>
                    {a.readFailed
                      ? t('trading.allowances.readFailed')
                      : a.unlimited
                        ? t('trading.allowances.unlimited')
                        : `${formatAmount(a.allowance)} ${a.token.symbol}`}
                  </b>
                  <small>
                    {a.balance !== null
                      ? `${formatAmount(a.balance)} ${t('trading.allowances.held')}`
                      : ''}
                    {a.exposureUsd !== null
                      ? ` · ${formatUsd(a.exposureUsd)} ${t('trading.allowances.atStake')}`
                      : ''}
                  </small>
                </div>
                <Button
                  variant={armed === key ? 'primary' : 'secondary'}
                  disabled={busy}
                  onClick={() => onRevoke(a)}
                  data-armed={armed === key || undefined}
                  data-testid="allowance-revoke"
                >
                  {busy
                    ? t('trading.allowances.revoking')
                    : armed === key
                      ? t('trading.allowances.revoke.confirm')
                      : t('trading.allowances.revoke')}
                </Button>
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}
