import {
  EMPTY_TRAY_SUMMARY,
  TRAY_COUNT_MAX,
  TRAY_LABEL_MAX,
  type TrayNextMandate,
  type TraySummary,
} from '@shared/tray'

/**
 * The renderer is sandboxed and the menu is OS chrome: every `tray:summary`
 * is checked before it reaches a label. Counts become whole numbers in
 * 0..TRAY_COUNT_MAX, the mandate label one line of at most TRAY_LABEL_MAX
 * characters, the time a real instant. Anything else is dropped.
 */
export function sanitizeTraySummary(raw: unknown): TraySummary {
  if (!raw || typeof raw !== 'object') return { ...EMPTY_TRAY_SUMMARY }
  const obj = raw as Record<string, unknown>
  const approvalsPending = clampCount(obj.approvalsPending)
  return {
    liveTurns: clampCount(obj.liveTurns),
    approvalsPending,
    // A part of the whole, never more than it.
    tradeApprovals: Math.min(clampCount(obj.tradeApprovals), approvalsPending),
    nextMandate: sanitizeMandate(obj.nextMandate),
  }
}

export function clampCount(value: unknown): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) return 0
  return Math.min(TRAY_COUNT_MAX, Math.max(0, Math.floor(value)))
}

/** One line, no control characters, cut with an ellipsis past `max`. */
export function oneLine(value: unknown, max: number): string {
  if (typeof value !== 'string') return ''
  const flat = value.replace(/[\u0000-\u001f\u007f-\u009f\u2028\u2029]+/g, ' ')
  const text = flat.replace(/\s+/g, ' ').trim()
  return text.length > max ? `${text.slice(0, max - 1).trimEnd()}…` : text
}

function sanitizeMandate(raw: unknown): TrayNextMandate | null {
  if (!raw || typeof raw !== 'object') return null
  const obj = raw as Record<string, unknown>
  const label = oneLine(obj.label, TRAY_LABEL_MAX)
  if (!label || typeof obj.at !== 'string' || obj.at.length > 64) return null
  const at = Date.parse(obj.at)
  if (!Number.isFinite(at)) return null
  return { label, at: new Date(at).toISOString() }
}
