import { create } from 'zustand'
import type { SessionRow } from './sessions'

/**
 * One row as drawn. A pinned chat filed in an open folder is drawn twice (in
 * its folder and in the Pinned section), so a Shift-click ranges from the
 * copy that was clicked, not from the key's first appearance.
 */
export interface ListSlot {
  row: SessionRow
  /** Drawn inside a project folder. */
  nested: boolean
}

/** Where a copy of a row sits: key plus folder or list. */
export function slotId(key: string, nested: boolean): string {
  return `${nested ? 'folder' : 'list'}|${key}`
}

/**
 * The ids from `anchor` to `id`, both included, in the order `order` shows
 * them. Just `id` when the anchor is not on screen; nothing when `id` is not.
 */
export function rangeKeys(order: readonly string[], anchor: string | null, id: string): string[] {
  const to = order.indexOf(id)
  if (to < 0) return []
  const from = anchor === null ? -1 : order.indexOf(anchor)
  if (from < 0) return [id]
  return order.slice(Math.min(from, to), Math.max(from, to) + 1)
}

interface SessionSelectionStore {
  /** The rows the sidebar shows right now, top to bottom, each once. */
  visible: readonly SessionRow[]
  /** Every drawn copy, top to bottom, as `slotId`s. */
  order: readonly string[]
  /** Selected session keys; always a subset of `visible`. */
  keys: ReadonlySet<string>
  /** Where a Shift-click range starts (a slot id): the last row clicked without Shift. */
  anchor: string | null
  /** The rows the batch delete alert is asking about, until it goes. */
  pendingDelete: readonly SessionRow[] | null
  /** The list changed: drop whatever it no longer shows. */
  setVisible(slots: readonly ListSlot[]): void
  /** A plain click: nothing selected, and the row becomes the anchor. */
  click(key: string, nested: boolean): void
  /** Cmd-click: in or out of the selection, and the new anchor. */
  toggle(key: string, nested: boolean): void
  /** Shift-click: exactly the rows from the anchor to this one. */
  extend(key: string, nested: boolean): void
  /** Replace the selection (a right-click outside it, the rows a delete kept). */
  select(keys: Iterable<string>): void
  /** Cmd+A: every row on screen. */
  selectAll(): void
  clear(): void
  askDelete(rows: readonly SessionRow[]): void
  closeDelete(): void
}

const keyOf = (id: string) => id.slice(id.indexOf('|') + 1)

/**
 * Which sidebar rows are selected, the way Finder and Mail select in a list.
 * Kept apart from the open chat: a plain click still navigates and clears it.
 * Only rows on screen can be selected, so a delete never takes one unseen: a
 * search, a filter or a closed folder that hides a row also deselects it.
 */
export const useSessionSelection = create<SessionSelectionStore>((set, get) => {
  const shown = (key: string) => get().visible.some((r) => r.key === key)
  return {
    visible: [],
    order: [],
    keys: new Set(),
    anchor: null,
    pendingDelete: null,
    setVisible: (slots) => {
      const { keys, anchor } = get()
      const visible: SessionRow[] = []
      const on = new Set<string>()
      for (const { row } of slots) {
        if (on.has(row.key)) continue
        on.add(row.key)
        visible.push(row)
      }
      const order = slots.map((s) => slotId(s.row.key, s.nested))
      const kept = [...keys].filter((k) => on.has(k))
      set({
        visible,
        order,
        keys: kept.length === keys.size ? keys : new Set(kept),
        anchor: anchor !== null && order.includes(anchor) ? anchor : null,
      })
    },
    click: (key, nested) => set({ keys: new Set(), anchor: slotId(key, nested) }),
    toggle: (key, nested) => {
      if (!shown(key)) return
      const next = new Set(get().keys)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      set({ keys: next, anchor: slotId(key, nested) })
    },
    extend: (key, nested) => {
      const { order, anchor } = get()
      const id = slotId(key, nested)
      const range = rangeKeys(order, anchor, id)
      if (range.length === 0) return
      set({
        keys: new Set(range.map(keyOf)),
        anchor: anchor !== null && order.includes(anchor) ? anchor : id,
      })
    },
    select: (keys) => {
      const next = new Set([...keys].filter(shown))
      const first = get().order.find((id) => next.has(keyOf(id)))
      set({ keys: next, anchor: first ?? get().anchor })
    },
    selectAll: () => set({ keys: new Set(get().visible.map((r) => r.key)) }),
    clear: () => {
      if (get().keys.size > 0) set({ keys: new Set() })
    },
    askDelete: (rows) => set({ pendingDelete: rows }),
    closeDelete: () => set({ pendingDelete: null }),
  }
})

/** The selected rows, in the order the sidebar shows them. */
export function selectedRows(
  state: Pick<SessionSelectionStore, 'visible' | 'keys'> = useSessionSelection.getState(),
): SessionRow[] {
  return state.visible.filter((r) => state.keys.has(r.key))
}
