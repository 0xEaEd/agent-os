import { toast } from 'sonner'

/**
 * The toast a decision on an order leaves, one per order (`trd-order-<id>`),
 * wherever it was decided (the desk's card, the BOOK, the Trading tab).
 *
 * "Approved. Sending…" is a promise about what happens next, so it must not
 * outlive the order: `trading.order.finished` for that order takes it down
 * (the finished notification says how it ended), and it never stays up past
 * SENDING_TOAST_MAX_MS whatever sonner's own timer does — sonner pauses it
 * while the pointer rests on the stack or the window is hidden, and the toast
 * used to sit there for minutes after the order had confirmed.
 */

export const SENDING_TOAST_MAX_MS = 20_000

const sending = new Map<string, ReturnType<typeof setTimeout>>()

export function orderToastId(orderId: string): string {
  return `trd-order-${orderId}`
}

function settle(orderId: string): void {
  const timer = sending.get(orderId)
  if (timer === undefined) return
  clearTimeout(timer)
  sending.delete(orderId)
}

/** "Swap approved. Re-quoting and sending… · …": up until the order finishes. */
export function toastOrderSending(orderId: string, text: string): void {
  settle(orderId)
  const id = orderToastId(orderId)
  toast.success(text, { id })
  sending.set(
    orderId,
    setTimeout(() => {
      sending.delete(orderId)
      toast.dismiss(id)
    }, SENDING_TOAST_MAX_MS),
  )
}

/**
 * "Collect fees rejected · …": a decision, not a success — the warning icon,
 * never the check mark.
 */
export function toastOrderRejected(orderId: string, text: string): void {
  settle(orderId)
  toast.warning(text, { id: orderToastId(orderId) })
}

/** Any other toast about the order (a failed or repeated decision) replaces the sending one. */
export function toastOrder(kind: 'info' | 'error', orderId: string, text: string): void {
  settle(orderId)
  toast[kind](text, { id: orderToastId(orderId) })
}

/** `trading.order.finished`: a "Sending…" toast for this order has said all it can. */
export function orderToastFinished(orderId: string | null | undefined): void {
  if (!orderId || !sending.has(orderId)) return
  settle(orderId)
  toast.dismiss(orderToastId(orderId))
}
