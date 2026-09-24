import { ChevronLeft, ChevronRight, RefreshCw } from 'lucide-react'
import { useState } from 'react'
import { t } from '~/i18n'
import { useAllowances, useNetwork } from '~/stores/trading'
import { Allowances } from '../Allowances'
import { pipState, pipTitle } from '../NetworkPips'
import { chainShort } from '../logic'
import { Spinner } from '../parts'
import { DESK_TOOLS, type DeskToolId } from './ToolsPicker'

/**
 * The desk's tools, all of them, in one tab of the BOOK: a card each, with
 * the one figure that says whether it needs you (unlimited allowances, a
 * chain that is behind). Send, Multisend and Inspect open their sheets;
 * Allowances opens in place; Network is read right off its card. Nothing
 * here is a popup — it is where a person looks when they do not know what
 * the desk can do. The cards are the picker's own list, so the two never
 * disagree about what the desk offers.
 */
export function ToolsPanel({
  wallet,
  onSend,
  onMultisend,
  onInspect,
  onBurn,
}: {
  wallet: string | undefined
  onSend: () => void
  /** Opens the send sheet with the list leading; falls back to `onSend`. */
  onMultisend?: () => void
  onInspect: () => void
  onBurn: () => void
}) {
  const [view, setView] = useState<'cards' | 'allowances'>('cards')
  const allowances = useAllowances(wallet, undefined, true)
  const network = useNetwork(true)

  if (view === 'allowances') {
    return (
      <div className="trd-tools" data-testid="tools-panel">
        <button
          type="button"
          className="trd-tools__back app-no-drag"
          onClick={() => setView('cards')}
          data-testid="tools-back"
        >
          <ChevronLeft className="size-3.5" strokeWidth={2} aria-hidden />
          {t('trading.tools.heading')}
        </button>
        <Allowances wallet={wallet} />
      </div>
    )
  }

  const unlimited = allowances.unlimitedCount
  const chains = network.chains
  const unhealthy = chains.filter((c) => pipState(c) !== 'ok')

  const open: Record<DeskToolId, () => void> = {
    send: onSend,
    multisend: onMultisend ?? onSend,
    allowances: () => setView('allowances'),
    inspect: onInspect,
    network: () => void network.refetch(),
    burn: onBurn,
  }

  // The live line on the two cards that have one; the hint otherwise.
  const line: Partial<Record<DeskToolId, string>> = {
    allowances:
      allowances.isPending && !allowances.data
        ? t('trading.tools.reading')
        : unlimited > 0
          ? `${unlimited} ${unlimited === 1 ? t('trading.allowances.warn') : t('trading.allowances.warn.plural')}`
          : allowances.allowances.length
            ? `${allowances.allowances.length} ${t('trading.tools.allowances.live')}`
            : t('trading.tools.allowances.none'),
    network: chains.length
      ? unhealthy.length
        ? `${unhealthy.map((c) => c.name).join(', ')} · ${t(`trading.network.${pipState(unhealthy[0]!)}`)}`
        : t('trading.network.allHealthy')
      : t('trading.network.unknown'),
  }
  const tone: Partial<Record<DeskToolId, 'warn' | 'danger'>> = {
    allowances: unlimited > 0 ? 'warn' : undefined,
    network: unhealthy.length ? 'warn' : undefined,
    burn: 'danger',
  }

  return (
    <div className="trd-tools" data-testid="tools-panel">
      <p className="trd-tools__lead">{t('trading.tools.lead')}</p>

      {DESK_TOOLS.map((card) => {
        const Icon = card.icon
        if (card.id === 'network') {
          return (
            <section
              key={card.id}
              className="trd-tool trd-tool--static"
              data-tone={tone.network}
              aria-label={t(card.name)}
              data-testid="tool-network"
            >
              <Icon className="trd-tool__icon" strokeWidth={1.75} aria-hidden />
              <span className="trd-tool__text">
                <b>{t(card.name)}</b>
                <span>{line.network}</span>
              </span>
              <button
                type="button"
                className="trd-tool__refresh app-no-drag"
                onClick={open.network}
                disabled={network.isFetching}
                aria-label={t('trading.network.refresh')}
                title={t('trading.network.refresh')}
                data-testid="tool-network-refresh"
              >
                {network.isFetching ? (
                  <Spinner className="size-3" />
                ) : (
                  <RefreshCw className="size-3" strokeWidth={1.75} aria-hidden />
                )}
              </button>
              <ul className="trd-tool__chains">
                {chains.map((c) => (
                  <li key={c.chainId} data-state={pipState(c)} title={pipTitle(c)}>
                    <span className="trd-pip__dot" aria-hidden />
                    <b>{chainShort(c.chainId)}</b>
                    {/* The head is the least useful figure when space is short,
                        so it is the one that goes: the rest wrap rather than clip. */}
                    <span className="trd-mono">
                      {c.blockNumber !== null ? (
                        <span className="trd-tool__head">#{c.blockNumber} · </span>
                      ) : null}
                      {c.blockAgeS !== null ? `${c.blockAgeS}s` : '—'}
                      {c.baseFeeGwei !== null ? ` · ${gwei(c.baseFeeGwei)} gwei` : ''}
                      {c.latencyMs !== null ? ` · ${c.latencyMs} ms` : ''}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          )
        }
        return (
          <button
            key={card.id}
            type="button"
            className="trd-tool app-no-drag"
            data-tone={tone[card.id]}
            onClick={open[card.id]}
            data-testid={`tool-${card.id}`}
          >
            <Icon className="trd-tool__icon" strokeWidth={1.75} aria-hidden />
            <span className="trd-tool__text">
              <b>{t(card.name)}</b>
              <span>{line[card.id] ?? t(card.hint)}</span>
            </span>
            {card.id === 'allowances' && allowances.scanning ? (
              <Spinner className="size-3" />
            ) : (
              <ChevronRight className="trd-tool__go" strokeWidth={2} aria-hidden />
            )}
          </button>
        )
      })}
    </div>
  )
}

function gwei(value: number): string {
  if (value >= 1) return value.toFixed(2)
  if (value >= 0.001) return value.toFixed(4)
  return value.toPrecision(2)
}
