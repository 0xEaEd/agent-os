import { requireBiometric, type GateKind } from '~/lib/biometric-gate'
import { t } from '~/i18n'
import { askRisk, HIGH_RISK_USD, orderKind, orderKindWord, orderLine } from './desk/desk-logic'
import { chainName, formatUsd, shortAddress } from './logic'
import type { Mandate, MandatePayload, Order } from './types'

/**
 * What the Touch ID sheet says, and which gate kind applies, for each action
 * the gate stands in front of. The reason names the action, so the person at
 * the sensor knows what the fingerprint approves.
 */
export interface TouchIdAsk {
  kind: Extract<GateKind, 'approve' | 'approve-high'>
  reason: string
}

function fill(text: string, values: Record<string, string>): string {
  return text.replace(/\{(\w+)\}/g, (whole, key: string) => values[key] ?? whole)
}

/**
 * An order's approval: the card's own risk (the batch total for a
 * multisend), and "approve 0.05 ETH → USDC on Base". A leg of a batch whose
 * other legs are not known is treated as high-risk: the engine approves
 * every leg at once, and the part in hand cannot say what the whole is worth.
 */
export function orderTouchId(order: Order, legs?: readonly Order[]): TouchIdAsk {
  const orders = legs && legs.length ? [...legs] : [order]
  const batchUnknown = Boolean(order.batchId) && !(legs && legs.length)
  let totalUsd: number | null = 0
  for (const o of orders) {
    if (o.valueUsd === null) {
      totalUsd = null
      break
    }
    totalUsd += o.valueUsd
  }
  const risk = askRisk({ kind: orderKind(order), lead: order, totalUsd })
  const word = orderKindWord(order, orders.length)
  const line = orderLine(order, orders, t('trading.send.count'))
  const what = word === 'swap' ? line : `${t(`trading.card.kind.${word}`).toLowerCase()} ${line}`
  return {
    kind: risk === 'high' || batchUnknown ? 'approve-high' : 'approve',
    reason: fill(t('trading.touchId.reason.order'), { what, chain: chainName(order.chainId) }),
  }
}

/**
 * What a mandate may spend at most: its USD cap, else its buys × USD per buy;
 * null when neither is known.
 */
export function mandateCeilingUsd(mandate: Pick<Mandate, 'budget' | 'runs'>): number | null {
  const positive = (n: unknown): n is number => typeof n === 'number' && Number.isFinite(n) && n > 0
  const cap = mandate.budget?.capUsd
  if (positive(cap)) return cap
  const per = mandate.budget?.usdPerRun
  const max = mandate.runs?.max
  return positive(per) && positive(max) ? per * max : null
}

/**
 * A DCA mandate's approval: a standing permission to spend, high-risk when
 * what it may spend reaches the card's high-risk line or is unknown.
 */
export function mandateTouchId(mandate: Mandate): TouchIdAsk {
  const ceiling = mandateCeilingUsd(mandate)
  return {
    kind: ceiling === null || ceiling >= HIGH_RISK_USD ? 'approve-high' : 'approve',
    reason: fill(t('trading.touchId.reason.mandate'), {
      name: `“${mandate.name}”`,
      cap: formatUsd(ceiling),
      chain: mandate.chain?.name || chainName(mandate.chain?.id ?? 0),
    }),
  }
}

/** "export the private key of Main (0x1234…abcd)" / "remove the wallet …". */
export function vaultReason(
  action: 'export' | 'exportKeystore' | 'remove',
  wallet: { address: string; label?: string | null },
): string {
  const short = shortAddress(wallet.address)
  const label = wallet.label?.trim()
  return fill(t(`trading.touchId.reason.${action}`), {
    wallet: label ? `${label} (${short})` : short,
  })
}

/**
 * Touch ID before `trading.dca.approve` from a chat DCA card, which knows
 * only the mandate id: the mandate is read first so the sheet can name it
 * and the risk is the card's. Unreadable → treated as high-risk. Throws
 * `TouchIdDeclined` (already toasted) when the prompt did not confirm.
 */
export async function requireMandateTouchId(
  call: (method: string, params: Record<string, unknown>) => Promise<unknown>,
  mandateId: string,
): Promise<void> {
  let mandate: Mandate | null = null
  try {
    const res = (await call('trading.dca.get', { mandateId })) as MandatePayload | null
    mandate = res?.mandate ?? null
  } catch {
    /* unreadable: the high-risk ask below */
  }
  const ask: TouchIdAsk = mandate
    ? mandateTouchId(mandate)
    : {
        kind: 'approve-high',
        reason: fill(t('trading.touchId.reason.mandateId'), { id: mandateId }),
      }
  await requireBiometric(ask.kind, ask.reason, `mandate:${mandateId}`)
}
