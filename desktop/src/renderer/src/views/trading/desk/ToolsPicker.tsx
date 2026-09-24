import {
  Activity,
  Flame,
  ListChecks,
  Search,
  SendHorizontal,
  ShieldCheck,
  ShieldOff,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { t, type MessageKey } from '~/i18n'
import { useAllowances, useNetwork } from '~/stores/trading'
import { pipState } from '../NetworkPips'
import { Sheet } from '../parts'

export type DeskToolId = 'send' | 'multisend' | 'allowances' | 'inspect' | 'network' | 'burn'

type ToolGroup = 'move' | 'safety' | 'watch' | 'clean'

interface ToolCard {
  id: DeskToolId
  group: ToolGroup
  icon: LucideIcon
  name: MessageKey
  hint: MessageKey
  /** Reads only: never signs, never spends. */
  readOnly?: boolean
  /** Destroys something for good: badged, and last in the catalogue. */
  destructive?: boolean
}

// `clean` is last on purpose: the one destructive card should be the one a
// person has to scroll to, not the one they brush past on the way to Send.
const TOOL_GROUPS: readonly ToolGroup[] = ['move', 'safety', 'watch', 'clean']

export const DESK_TOOLS: readonly ToolCard[] = [
  {
    id: 'send',
    group: 'move',
    icon: SendHorizontal,
    name: 'trading.tool.send.name',
    hint: 'trading.tool.send.hint',
  },
  {
    id: 'multisend',
    group: 'move',
    icon: ListChecks,
    name: 'trading.tool.multisend.name',
    hint: 'trading.tool.multisend.hint',
  },
  {
    id: 'allowances',
    group: 'safety',
    icon: ShieldOff,
    name: 'trading.tool.allowances.name',
    hint: 'trading.tool.allowances.hint',
  },
  {
    id: 'inspect',
    group: 'safety',
    icon: Search,
    name: 'trading.tool.inspect.name',
    hint: 'trading.tool.inspect.hint',
    readOnly: true,
  },
  {
    id: 'network',
    group: 'watch',
    icon: Activity,
    name: 'trading.tool.network.name',
    hint: 'trading.tool.network.hint',
    readOnly: true,
  },
  {
    id: 'burn',
    group: 'clean',
    icon: Flame,
    name: 'trading.tool.burn.name',
    hint: 'trading.tool.burn.hint',
    destructive: true,
  },
]

/**
 * The tools catalogue, cut like the mission catalogue: groups, a card per
 * tool, the read-only ones badged so a person knows which cards cannot
 * spend. Two cards wear a live figure — how many unlimited allowances, and
 * whether a chain is behind — because that is the reason to open them.
 */
export function ToolsPicker({
  wallet,
  onPick,
  onClose,
}: {
  wallet: string | undefined
  onPick: (tool: DeskToolId) => void
  onClose: () => void
}) {
  const allowances = useAllowances(wallet)
  const network = useNetwork()
  const unhealthy = network.chains.filter((c) => pipState(c) !== 'ok')
  const live: Partial<Record<DeskToolId, { text: string; tone: 'warn' | 'ok' }>> = {}
  if (allowances.data) {
    live.allowances =
      allowances.unlimitedCount > 0
        ? {
            text: `${allowances.unlimitedCount} ${allowances.unlimitedCount === 1 ? t('trading.allowances.warn') : t('trading.allowances.warn.plural')}`,
            tone: 'warn',
          }
        : { text: t('trading.tools.allowances.none'), tone: 'ok' }
  }
  if (network.chains.length) {
    live.network = unhealthy.length
      ? {
          text: `${unhealthy.map((c) => c.name).join(', ')} · ${t(`trading.network.${pipState(unhealthy[0]!)}`)}`,
          tone: 'warn',
        }
      : { text: t('trading.network.allHealthy'), tone: 'ok' }
  }
  return (
    <Sheet title={t('trading.tool.pick.title')} onClose={onClose} widest>
      <div className="trd-picker" data-testid="tools-picker">
        <p className="trd-picker__lead">{t('trading.tool.pick.lead')}</p>
        {TOOL_GROUPS.map((group) => {
          const cards = DESK_TOOLS.filter((c) => c.group === group)
          return (
            <section key={group} className="trd-picker__group" data-group={group}>
              <h3>{t(`trading.tool.group.${group}`)}</h3>
              <div className="trd-picker__grid">
                {cards.map((card) => {
                  const Icon = card.icon
                  const fact = live[card.id]
                  return (
                    <button
                      key={card.id}
                      type="button"
                      className="trd-picker__card app-no-drag"
                      data-tone={
                        card.destructive ? 'danger' : fact?.tone === 'warn' ? 'warn' : undefined
                      }
                      onClick={() => onPick(card.id)}
                      data-testid={`tool-${card.id}`}
                    >
                      <Icon className="size-4" strokeWidth={1.75} aria-hidden />
                      <span className="trd-picker__text">
                        <span className="trd-picker__title">
                          <b>{t(card.name)}</b>
                          {card.readOnly ? (
                            <span className="trd-picker__safe">
                              <ShieldCheck className="size-3" strokeWidth={2} aria-hidden />
                              {t('trading.preset.readOnly')}
                            </span>
                          ) : null}
                          {card.destructive ? (
                            <span className="trd-picker__danger">
                              <Flame className="size-3" strokeWidth={2} aria-hidden />
                              {t('trading.tool.destructive')}
                            </span>
                          ) : null}
                        </span>
                        <span>{t(card.hint)}</span>
                        {fact ? (
                          <span
                            className="trd-picker__live trd-mono"
                            data-tone={fact.tone}
                            data-testid={`tool-${card.id}-live`}
                          >
                            {fact.text}
                          </span>
                        ) : null}
                      </span>
                    </button>
                  )
                })}
              </div>
            </section>
          )
        })}
      </div>
    </Sheet>
  )
}
