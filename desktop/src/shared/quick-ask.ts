/**
 * Quick Ask: a global hotkey opens a small prompt over any app, and what is
 * typed there lands in the main window's chat. These are the pieces every
 * process agrees on: the shortcut list, the submission payload and its
 * validation, and what main reports about the hotkey's registration.
 */

/** The keys Quick Ask can live on. A fixed list, not free rebinding. */
export const QUICK_ASK_SHORTCUTS = [
  'Alt+Space',
  'Control+Space',
  'CommandOrControl+Shift+Space',
] as const
export type QuickAskShortcut = (typeof QUICK_ASK_SHORTCUTS)[number]
export const DEFAULT_QUICK_ASK_SHORTCUT: QuickAskShortcut = 'Alt+Space'

export function isQuickAskShortcut(value: unknown): value is QuickAskShortcut {
  return typeof value === 'string' && (QUICK_ASK_SHORTCUTS as readonly string[]).includes(value)
}

/** How each accelerator is drawn as keycaps (Settings › Shortcuts, the pane). */
export const QUICK_ASK_KEYCAPS: Record<QuickAskShortcut, readonly string[]> = {
  'Alt+Space': ['⌥', 'Space'],
  'Control+Space': ['⌃', 'Space'],
  'CommandOrControl+Shift+Space': ['⌘', '⇧', 'Space'],
}

/** `new`: a fresh session. `current`: the session the main window is on (or last opened). */
export type QuickAskTarget = 'new' | 'current'

export interface QuickAskSubmission {
  text: string
  target: QuickAskTarget
}

/** Upper bound on a submission, in UTF-8 bytes. Anything longer is refused. */
export const QUICK_ASK_MAX_BYTES = 20_000

export function utf8Length(text: string): number {
  return new TextEncoder().encode(text).length
}

/**
 * Validate a submission crossing IPC. The renderer is sandboxed, but its
 * payload is still untrusted: non-strings, empty or whitespace-only text,
 * text over `QUICK_ASK_MAX_BYTES` and unknown targets are all refused (null).
 * Accepted text is trimmed, the way the composer sends it.
 */
export function parseQuickAskSubmission(raw: unknown): QuickAskSubmission | null {
  if (!raw || typeof raw !== 'object') return null
  const obj = raw as Record<string, unknown>
  if (typeof obj.text !== 'string') return null
  if (obj.target !== 'new' && obj.target !== 'current') return null
  if (utf8Length(obj.text) > QUICK_ASK_MAX_BYTES) return null
  const text = obj.text.trim()
  if (!text) return null
  return { text, target: obj.target }
}

/** Bounds on the panel's content height the renderer may ask for, in CSS px. */
export const QUICK_ASK_MIN_HEIGHT = 56
export const QUICK_ASK_MAX_HEIGHT = 420

/** A reported content height, made safe to size a window with; null if it is not a number. */
export function clampPanelHeight(raw: unknown): number | null {
  if (typeof raw !== 'number' || !Number.isFinite(raw)) return null
  return Math.round(Math.min(QUICK_ASK_MAX_HEIGHT, Math.max(QUICK_ASK_MIN_HEIGHT, raw)))
}

/**
 * Where the hotkey stands. `off`: Quick Ask is disabled. `ready`: the key is
 * registered. `unavailable`: macOS refused it (another app holds the key).
 */
export interface QuickAskStatus {
  state: 'off' | 'ready' | 'unavailable'
  shortcut: QuickAskShortcut
}
