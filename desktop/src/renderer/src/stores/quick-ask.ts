import { create } from 'zustand'
import { canonicalSessionKey } from '@/views/chat/logic'
import type { QuickAskSubmission, QuickAskTarget } from '@shared/quick-ask'
import { desktopApi } from '~/lib/desktop-api'
import { currentSessionKey } from '~/lib/use-notifications'

/**
 * A Quick Ask submission on its way into a chat. Two steps, two owners:
 * the shell routes it (`dest` set, the window navigated there), then the
 * chat showing `dest` sends it and drops it. `dest` is a session key, or
 * `''` for a fresh session on the keyless home; undefined until routed.
 */
export interface QuickAskItem {
  id: number
  text: string
  target: QuickAskTarget
  dest?: string
}

interface QuickAskStore {
  /** Oldest first. Only the head is ever routed or sent. */
  queue: QuickAskItem[]
  receive(submissions: readonly QuickAskSubmission[]): void
  route(id: number, dest: string): void
  /** The chat sent it (or queued it behind a running reply). */
  done(id: number): void
}

let nextId = 1

/**
 * One-shot hand-off from the Quick Ask panel to the chat. Unlike
 * `useUi.pendingPrompt` (Skills → "Use in chat"), which only fills the
 * composer, an item here is sent as soon as its chat is ready.
 */
export const useQuickAsk = create<QuickAskStore>((set) => ({
  queue: [],
  receive: (submissions) => {
    if (submissions.length === 0) return
    set((s) => ({
      queue: [
        ...s.queue,
        ...submissions.map((sub) => ({ id: nextId++, text: sub.text, target: sub.target })),
      ],
    }))
  },
  route: (id, dest) =>
    set((s) => ({ queue: s.queue.map((item) => (item.id === id ? { ...item, dest } : item)) })),
  done: (id) => set((s) => ({ queue: s.queue.filter((item) => item.id !== id) })),
}))

/**
 * Where a submission goes. `new`: a fresh session (`''`, the keyless home,
 * the same path ⌘N and ⌘⇧O take). `current`: the session on screen; off a
 * chat (home, a project, the jobs sheet) the last session opened; with no
 * session ever opened, a fresh one.
 */
export function quickAskDestination(
  target: QuickAskTarget,
  pathname: string,
  lastSession: string | null,
): string {
  if (target === 'new') return ''
  const key = currentSessionKey(pathname) ?? lastSession
  return key ? canonicalSessionKey(key) : ''
}

/**
 * Collect what main is holding for this window: on every ping, and once at
 * bind time, since a window created (or reloaded) for a submission mounts
 * after main has already queued it. Writes go to the store whether or not
 * the caller is still mounted, so nothing collected is ever dropped.
 */
export function bindQuickAskDelivery(): () => void {
  const api = desktopApi()
  const collect = () => {
    void api.quickAsk
      .take()
      .then((items) => useQuickAsk.getState().receive(items))
      .catch(() => {})
  }
  const off = api.quickAsk.onDeliver(collect)
  collect()
  return off
}
