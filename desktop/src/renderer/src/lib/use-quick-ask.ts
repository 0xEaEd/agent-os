import { useEffect, useRef, type RefObject } from 'react'
import { useLocation, useNavigate } from 'react-router'
import { sessionPath } from '~/components/sidebar/SessionRow'
import { readLastSession } from '~/lib/last-session'
import { bindQuickAskDelivery, quickAskDestination, useQuickAsk } from '~/stores/quick-ask'
import { useUi } from '~/stores/ui'

/**
 * The shell's half of Quick Ask: collect submissions from main, and take the
 * window to where the next one goes — closing any sheet over the chat — so
 * the chat there can send it (`useQuickAskSend`). Bound once, in AppShell.
 */
export function useQuickAskRouting(): void {
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const head = useQuickAsk((s) => s.queue[0])

  useEffect(() => bindQuickAskDelivery(), [])

  useEffect(() => {
    if (!head || head.dest !== undefined) return
    const dest = quickAskDestination(head.target, pathname, readLastSession())
    const ui = useUi.getState()
    ui.closeSettings()
    ui.closeJobs()
    ui.closeSkills()
    useQuickAsk.getState().route(head.id, dest)
    void navigate(dest ? sessionPath(dest) : '/sessions')
  }, [head, pathname, navigate])
}

/** Longest a routed submission waits for its transcript before it is sent anyway. */
export const QUICK_ASK_SETTLE_MS = 4_000

/**
 * The chat's half: when the head of the queue is routed to this chat
 * (`paramKey`, `''` on the keyless home), send it once the transcript has
 * settled. Sending before the session's history has been drawn would let
 * that first draw wipe the message just sent from the screen; the shared
 * transcript marks the moment with `data-history-ready` on its container.
 * If that never comes (a history read that hangs), it is sent after
 * `QUICK_ASK_SETTLE_MS` regardless: a late send beats a lost one.
 */
export function useQuickAskSend({
  paramKey,
  threadRef,
  send,
}: {
  paramKey: string
  threadRef: RefObject<HTMLElement | null>
  send: (text: string) => void
}): void {
  const head = useQuickAsk((s) => s.queue[0])
  const sendRef = useRef(send)
  useEffect(() => {
    sendRef.current = send
  }, [send])

  const ours = head !== undefined && head.dest === paramKey
  const headId = ours ? head.id : null

  useEffect(() => {
    if (headId === null) return
    let sent = false
    const fire = () => {
      if (sent) return
      const item = useQuickAsk.getState().queue[0]
      if (!item || item.id !== headId) return
      sent = true
      useQuickAsk.getState().done(item.id)
      sendRef.current(item.text)
    }
    const settled = () => threadRef.current?.dataset.historyReady === 'true'
    if (settled()) {
      fire()
      return
    }
    const thread = threadRef.current
    const observer = new MutationObserver(() => {
      if (settled()) fire()
    })
    if (thread)
      observer.observe(thread, { attributes: true, attributeFilter: ['data-history-ready'] })
    const timer = window.setTimeout(fire, QUICK_ASK_SETTLE_MS)
    return () => {
      observer.disconnect()
      window.clearTimeout(timer)
    }
  }, [headId, threadRef])
}
