import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import {
  MAX_PENDING,
  enqueuePending,
  popAllPendingIntoComposer as popAllModel,
  popPendingTail as popTailModel,
  type PendingAttachment,
  type PendingItem,
} from './logic'

const EMPTY_QUEUE: PendingItem[] = []

/**
 * The composer accessors the queue hook writes through when it drains / recovers
 * a pending item back into the input (chat.js: the legacy `_pendingQueue`
 * functions mutated `_textarea.value` / `_pendingAttachments` / `_pendingSessionIntent`
 * directly). Here those live in the composer + attachments + ChatPage, so the
 * hook takes them as callbacks and stays free of DOM ownership.
 */
export interface PendingComposerBridge {
  /** Read the current composer text (chat.js:8604 `_textarea.value`). */
  getComposerText: () => string
  /** Write the composer text + focus caret-at-end (chat.js:8608-8618). */
  setComposerText: (text: string) => void
  /** Read the current pending attachments (chat.js:8543 `_pendingAttachments`). */
  getAttachments: () => PendingAttachment[]
  /** Replace the pending attachments (chat.js:8546/8611). */
  setAttachments: (attachments: PendingAttachment[]) => void
  /** Read the per-send session intent (chat.js:8547 `_pendingSessionIntent`). */
  getIntent: () => string | null
  /** Write the per-send session intent (chat.js:8547/8612). */
  setIntent: (intent: string | null) => void
  /**
   * Send a recovered/drained head item (chat.js:8549 `_onSend`). Called only by
   * `drainQueueHead` on a natural terminal event. ChatPage wires this to its
   * normalize-then-send path.
   */
  sendDrainedHead: (text: string, attachments: PendingAttachment[], intent: string | null) => void
  /** True while a turn is streaming (chat.js:6091 `_isStreaming`). */
  isStreaming: () => boolean
  /** True while a compaction is in flight for the session (chat.js:6091). */
  isCompactInFlight: () => boolean
}

export interface UsePendingQueue {
  /** The live queue (oldest→newest), passed to `<PendingQueue />`. */
  queue: PendingItem[]
  /** The queue length — the composer's ESC/Alt guards + the count label read it. */
  length: number
  /**
   * chat.js:6091-6110 — enqueue a send while busy. Returns true when queued;
   * false + a "queue full" toast when at MAX_PENDING.
   */
  enqueue: (item: PendingItem, opts?: { toastMessage?: string; waitReason?: string }) => boolean
  /** Remove one item by index (chat.js:8459-8463). */
  remove: (idx: number) => void
  /** Clear the whole queue (chat.js:8466-8471). */
  clearAll: () => void
  /**
   * chat.js:8596 `_popAllPendingIntoComposer` — recover the whole queue into the
   * composer (ESC / abort / failed-terminal). Returns true if anything recovered.
   */
  popAllIntoComposer: () => boolean
  /** chat.js:8560 `_popPendingTail` — Alt+↑ pop the most-recent into the composer. */
  popTail: () => void
  /** chat.js:8644 `_schedulePendingDrainAfterTerminal` — debounced FIFO drain. */
  scheduleDrainAfterTerminal: () => void
  /** chat.js:8637 — clear the pending-drain timer (session switch / clear-all). */
  clearDrainTimer: () => void
}

/**
 * The chat pending-queue model + drain/recover controller (chat.js:8437-8663 +
 * 335). Owns the `_pendingQueue` state and every mutation that legacy scattered
 * across `_enqueuePendingInput` / `_drainQueueHead` / `_popAllPendingIntoComposer`
 * / `_popPendingTail` / `_schedulePendingDrainAfterTerminal`. The pure list math
 * lives in logic.ts (TDD'd); this hook layers the React state + the composer/
 * attachment writes + the debounce timer on top.
 */
export function usePendingQueue(bridge: PendingComposerBridge, sessionKey = ''): UsePendingQueue {
  // One queue per session. The chat route element stays mounted across a
  // session switch, so a single queue would follow the user into the next
  // session and drain there on its first terminal frame — including the
  // `.done` the gateway replays on subscribe. Each session keeps its own, and
  // a queue left behind drains when its session's turn ends while it is shown.
  const [queues, setQueues] = useState<ReadonlyMap<string, PendingItem[]>>(() => new Map())
  const queue = queues.get(sessionKey) ?? EMPTY_QUEUE
  // Mirror the queues + the shown key in refs so the debounce-timer callback +
  // the terminal delegates read the latest without re-arming on every change.
  // The queues ref is written only by `writeQueue`, never during render.
  const queuesRef = useRef<ReadonlyMap<string, PendingItem[]>>(queues)
  const sessionKeyRef = useRef(sessionKey)

  // The bridge is re-created each render; keep the latest in a ref so the stable
  // callbacks below always call through to fresh composer/attachment accessors.
  const bridgeRef = useRef(bridge)
  useEffect(() => {
    bridgeRef.current = bridge
  })

  const drainTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const clearDrainTimer = useCallback(() => {
    if (drainTimerRef.current) {
      clearTimeout(drainTimerRef.current)
      drainTimerRef.current = null
    }
  }, [])

  // A drain armed for the outgoing session must not fire into the next one.
  useEffect(() => {
    sessionKeyRef.current = sessionKey
    clearDrainTimer()
  }, [sessionKey, clearDrainTimer])

  const currentQueue = useCallback(
    () => queuesRef.current.get(sessionKeyRef.current) ?? EMPTY_QUEUE,
    [],
  )
  const writeQueue = useCallback((next: PendingItem[]) => {
    const map = new Map(queuesRef.current)
    if (next.length) map.set(sessionKeyRef.current, next)
    else map.delete(sessionKeyRef.current)
    queuesRef.current = map
    setQueues(map)
  }, [])

  // chat.js:8505-8533 `_enqueuePendingInput` — queue a send, clearing the
  // composer + attachments + intent on success (the payload has moved to the
  // queue). Full → reject + toast (chat.js:8511-8517).
  const enqueue = useCallback<UsePendingQueue['enqueue']>(
    (item, opts) => {
      const res = enqueuePending(currentQueue(), item)
      if (!res.ok) {
        toast.warning(
          `Pending queue full (${MAX_PENDING}). Wait for ${opts?.waitReason ?? 'the current response'} or clear.`,
          { duration: 3000 },
        )
        return false
      }
      writeQueue(res.queue)
      // chat.js:8525-8528 — the composer + attachments + intent are now empty.
      bridgeRef.current.setComposerText('')
      bridgeRef.current.setAttachments([])
      bridgeRef.current.setIntent(null)
      toast.info(opts?.toastMessage ?? `Queued (${res.queue.length}/${MAX_PENDING})`, {
        duration: 1500,
      })
      return true
    },
    [currentQueue, writeQueue],
  )

  // chat.js:8455-8463 — remove one chip.
  const remove = useCallback(
    (idx: number) => {
      const prev = currentQueue()
      if (idx < 0 || idx >= prev.length) return
      const next = prev.slice()
      next.splice(idx, 1)
      writeQueue(next)
    },
    [currentQueue, writeQueue],
  )

  // chat.js:8466-8471 — clear-all also cancels the pending-drain timer.
  const clearAll = useCallback(() => {
    clearDrainTimer()
    writeQueue([])
  }, [clearDrainTimer, writeQueue])

  // chat.js:8596-8626 `_popAllPendingIntoComposer` — recover the whole queue.
  const popAllIntoComposer = useCallback((): boolean => {
    clearDrainTimer()
    const b = bridgeRef.current
    const out = popAllModel(currentQueue(), b.getComposerText(), b.getAttachments(), b.getIntent())
    if (!out.recovered) return false
    writeQueue(out.queue)
    b.setComposerText(out.text)
    b.setAttachments(out.attachments)
    b.setIntent(out.intent)
    return true
  }, [clearDrainTimer, currentQueue, writeQueue])

  // chat.js:8560-8570 `_popPendingTail` — Alt+↑ recover the tail.
  const popTail = useCallback(() => {
    const out = popTailModel(currentQueue())
    if (!out.recovered) return
    writeQueue(out.queue)
    const b = bridgeRef.current
    b.setComposerText(out.text)
    b.setAttachments(out.attachments)
    b.setIntent(out.intent)
  }, [currentQueue, writeQueue])

  // chat.js:8535-8558 `_drainQueueHead` — FIFO shift → send the head, preserving
  // (and restoring after) any draft the user typed while the turn ran. Only fires
  // on a natural (non-aborted) terminal event, via the debounce below.
  const drainQueueHead = useCallback(() => {
    clearDrainTimer()
    const cur = currentQueue()
    if (cur.length === 0) return
    const [head, ...rest] = cur
    if (!head) return
    writeQueue(rest)
    const b = bridgeRef.current
    // chat.js:8542-8544 — snapshot the in-progress draft.
    const draftText = b.getComposerText()
    const draftAttachments = b.getAttachments().map((a) => ({ ...a }))
    const draftIntent = b.getIntent()
    // chat.js:8549 — send the head.
    b.sendDrainedHead(head.text || '', head.attachments || [], head.intent || null)
    // chat.js:8550-8556 — restore the draft the user was typing.
    if (draftText.trim() || draftAttachments.length || draftIntent) {
      b.setComposerText(draftText)
      b.setAttachments(draftAttachments)
      b.setIntent(draftIntent)
    }
  }, [clearDrainTimer, currentQueue, writeQueue])

  // chat.js:8644-8652 `_schedulePendingDrainAfterTerminal` — 50ms debounce, then
  // re-check that no stream/compaction resumed and the queue is non-empty before
  // draining the head.
  const scheduleDrainAfterTerminal = useCallback(() => {
    if (currentQueue().length === 0) return
    clearDrainTimer()
    drainTimerRef.current = setTimeout(() => {
      drainTimerRef.current = null
      const b = bridgeRef.current
      if (b.isStreaming() || b.isCompactInFlight() || currentQueue().length === 0) return
      drainQueueHead()
    }, 50)
  }, [clearDrainTimer, currentQueue, drainQueueHead])

  // Tear down the timer on unmount.
  useEffect(() => clearDrainTimer, [clearDrainTimer])

  return {
    queue,
    length: queue.length,
    enqueue,
    remove,
    clearAll,
    popAllIntoComposer,
    popTail,
    scheduleDrainAfterTerminal,
    clearDrainTimer,
  }
}
