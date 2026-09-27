import { ChevronLeft } from 'lucide-react'
import { Button } from '~/components/ui/button'
import { t } from '~/i18n'
import { useNetwork } from '~/stores/trading'
import { Allowances } from '../Allowances'
import { pipState, pipTitle } from '../NetworkPips'
import { ErrorState, Sheet, Spinner } from '../parts'

/** Allowances, as a sheet reached from the tools catalogue (the BOOK's Tools tab has it in place). */
export function AllowancesSheet({
  wallet,
  onBack,
  onClose,
}: {
  wallet: string | undefined
  onBack: () => void
  onClose: () => void
}) {
  return (
    <Sheet
      title={t('trading.allowances.title')}
      onClose={onClose}
      wide
      foot={
        <>
          <Button variant="ghost" onClick={onBack} data-testid="tool-back">
            <ChevronLeft className="size-3.5" strokeWidth={2} aria-hidden />
            {t('trading.tool.back')}
          </Button>
          <Button variant="secondary" onClick={onClose}>
            {t('trading.sheet.close')}
          </Button>
        </>
      }
    >
      <Allowances wallet={wallet} />
    </Sheet>
  )
}

/** Every chain's head, age, gas and RPC latency, read now. */
export function NetworkSheet({ onBack, onClose }: { onBack: () => void; onClose: () => void }) {
  const network = useNetwork(true, 10_000)
  return (
    <Sheet
      title={t('trading.network.title')}
      onClose={onClose}
      foot={
        <>
          <Button variant="ghost" onClick={onBack} data-testid="tool-back">
            <ChevronLeft className="size-3.5" strokeWidth={2} aria-hidden />
            {t('trading.tool.back')}
          </Button>
          <Button
            variant="secondary"
            disabled={network.isFetching}
            onClick={() => void network.refetch()}
            data-testid="network-refresh"
          >
            {network.isFetching ? <Spinner className="size-3.5" /> : null}
            {t('trading.network.refresh')}
          </Button>
        </>
      }
    >
      {network.isError && network.chains.length === 0 ? (
        <ErrorState error={network.error} onRetry={() => void network.refetch()} />
      ) : network.chains.length === 0 ? (
        <p className="trd-decode__empty">{t('trading.network.unknown')}</p>
      ) : (
        <ul className="trd-netlist" data-testid="network-sheet">
          {network.chains.map((c) => {
            const state = pipState(c)
            return (
              <li
                key={c.chainId}
                className="trd-netlist__row"
                data-state={state}
                title={pipTitle(c)}
              >
                <span className="trd-pip__dot" aria-hidden />
                <b>{c.name}</b>
                <span className="trd-netlist__state">
                  {state === 'ok'
                    ? t('trading.network.healthy')
                    : state === 'stale'
                      ? t('trading.network.stale')
                      : t('trading.network.down')}
                </span>
                <dl className="trd-netlist__facts trd-mono">
                  <div>
                    <dt>{t('trading.network.head')}</dt>
                    <dd>{c.blockNumber !== null ? `#${c.blockNumber}` : '—'}</dd>
                  </div>
                  <div>
                    <dt>{t('trading.network.age')}</dt>
                    <dd>{c.blockAgeS !== null ? `${c.blockAgeS} s` : '—'}</dd>
                  </div>
                  <div>
                    <dt>{t('trading.network.gas')}</dt>
                    <dd>{c.baseFeeGwei !== null ? `${gwei(c.baseFeeGwei)} gwei` : '—'}</dd>
                  </div>
                  <div>
                    <dt>{t('trading.network.latency')}</dt>
                    <dd>{c.latencyMs !== null ? `${c.latencyMs} ms` : '—'}</dd>
                  </div>
                </dl>
                {c.error ? <p className="trd-netlist__error">{c.error}</p> : null}
                <span className="trd-netlist__rpc">{c.rpcUrl}</span>
              </li>
            )
          })}
        </ul>
      )}
    </Sheet>
  )
}

function gwei(value: number): string {
  if (value >= 1) return value.toFixed(2)
  if (value >= 0.001) return value.toFixed(4)
  return value.toPrecision(2)
}
