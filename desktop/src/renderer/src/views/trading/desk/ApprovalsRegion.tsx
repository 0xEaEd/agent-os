import { useEffect, useMemo, useRef, useState } from 'react'
import { t } from '~/i18n'
import type { Order, Wallet } from '../types'
import { ApprovalCard } from './ApprovalCard'
import { groupAsks } from './desk-logic'

/**
 * Where the agent's asks dock: between the transcript and the composer, at
 * the point of cause. Height-bounded so a queue of asks can never push the
 * composer off-screen; the region scrolls inside. A card that settles stays
 * for a while as a one-line stamp so the outcome is read where the ask was.
 * The legs of a multisend are one ask and one card.
 */
export function ApprovalsRegion({
  pending,
  settled,
  wallets,
  deciding,
  onApprove,
  onReject,
  focusOrderId,
  onDismiss,
}: {
  pending: Order[]
  settled: Order[]
  wallets: readonly Wallet[]
  deciding: string | null
  onApprove: (order: Order) => void
  onReject: (order: Order, reason: string) => void
  /** From a notification: scroll this order's card into view. */
  focusOrderId: string | null
  /** Close a settled stamp before its own clock runs out. */
  onDismiss?: (orderId: string) => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  const asks = useMemo(() => groupAsks(pending), [pending])
  const stamps = useMemo(() => groupAsks(settled), [settled])
  // Reject gets focus once, on the first card that appears — never on every render.
  const [firstSeen, setFirstSeen] = useState<string | null>(null)
  const first = asks[0]?.lead.orderId ?? null
  if (first && first !== firstSeen) setFirstSeen(first)

  useEffect(() => {
    if (!focusOrderId) return
    // The focused order may be any leg; its card is keyed by the batch's lead.
    const ask = asks.find((a) => a.orders.some((o) => o.orderId === focusOrderId))
    const target = ask?.lead.orderId ?? focusOrderId
    const el = ref.current?.querySelector<HTMLElement>(`[data-order="${target}"]`)
    el?.scrollIntoView({ block: 'nearest' })
  }, [focusOrderId, asks])

  if (asks.length === 0 && stamps.length === 0) return null
  return (
    <div
      ref={ref}
      className="trd-asks"
      role="region"
      aria-label={t('trading.card.region')}
      data-testid="approvals-region"
    >
      {asks.map((ask) => (
        <ApprovalCard
          key={ask.key}
          order={ask.lead}
          legs={ask.orders}
          wallets={wallets}
          deciding={ask.orders.some((o) => deciding === o.orderId)}
          onApprove={onApprove}
          onReject={onReject}
          focusOnMount={
            ask.lead.orderId === firstSeen || ask.orders.some((o) => o.orderId === focusOrderId)
          }
        />
      ))}
      {stamps.map((ask) => (
        <div key={ask.key} className="trd-asks__stamp" data-testid="approval-stamp">
          <ApprovalCard
            order={ask.lead}
            legs={ask.orders}
            wallets={wallets}
            deciding={false}
            onApprove={() => {}}
            onReject={() => {}}
            focusOnMount={false}
            onDismiss={
              onDismiss ? () => ask.orders.forEach((o) => onDismiss(o.orderId)) : undefined
            }
          />
        </div>
      ))}
    </div>
  )
}
