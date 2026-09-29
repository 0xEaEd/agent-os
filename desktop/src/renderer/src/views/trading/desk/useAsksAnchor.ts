import { useLayoutEffect, type RefObject } from 'react'

/** The height the region takes from the column: its box plus its margins. */
function footprint(el: HTMLElement): number {
  const style = getComputedStyle(el)
  return (
    el.getBoundingClientRect().height +
    (parseFloat(style.marginTop) || 0) +
    (parseFloat(style.marginBottom) || 0)
  )
}

/**
 * Keep the foot of the transcript in view when the asks region grows.
 *
 * The region is a flex sibling of `.chat-thread` inside `.chat-stage`, so every
 * pixel it gains is taken from the transcript's viewport — at its bottom edge.
 * A scroll container keeps its top edge fixed when it shrinks, so whatever sat
 * at the foot of the transcript (the newest message, an inline card's Approve
 * row) slid out of view behind the card that had just docked, unless the
 * reader happened to be pinned to the tail. Scrolling the transcript by the
 * same amount keeps the reader's distance from the bottom exactly where it was:
 * pinned stays pinned, and a reader a little way up still sees the same last
 * lines. Shrinking needs nothing — the transcript simply gains room below.
 */
export function useAsksAnchor(ref: RefObject<HTMLElement | null>, shown: boolean): void {
  useLayoutEffect(() => {
    const region = ref.current
    if (!shown || !region) return
    const thread = region.closest('.chat-stage')?.querySelector<HTMLElement>('.chat-thread')
    if (!thread) return
    let last = 0
    const settle = () => {
      const next = footprint(region)
      const grew = next - last
      last = next
      if (grew > 0) thread.scrollTop += grew
    }
    // The region just mounted and already took its room: pay it back before paint.
    settle()
    // Feature-detected — jsdom has no ResizeObserver.
    if (typeof ResizeObserver !== 'function') return
    const ro = new ResizeObserver(settle)
    ro.observe(region)
    return () => ro.disconnect()
  }, [ref, shown])
}
