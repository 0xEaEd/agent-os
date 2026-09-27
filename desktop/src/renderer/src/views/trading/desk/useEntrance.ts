import { useEffect, useState } from 'react'
import { useReducedMotion } from 'motion/react'
import { ENTRANCE_MS, LEAVE_MS, shouldPlayEntrance, type DeskMode } from './mode-logic'

export type EnterPhase = 'trading' | 'chat' | null

/**
 * Drives `data-enter` on the mode shell: "trading" for the desk's power-on
 * choreography, "chat" for the short reverse, null at rest.
 *
 * The phase is decided DURING RENDER, not in an effect, and that is the whole
 * point of this hook. An effect runs after the browser has painted, so the
 * first frame of the desk was painted with no `data-enter` on the shell — the
 * instruments showed at their final position — and the attribute only landed
 * on the frame after, where every `both`-filled keyframe yanked them back to
 * its from-state and started over. Content appearing, jumping back, then
 * fading in is the stutter people saw, and it was loudest coming from a fresh
 * chat, because there the composer's centre-to-bottom Motion spring was also
 * running: `entering` reached ChatView one commit too late to snap it, so the
 * composer flew across the pane while the panels rewound underneath it.
 *
 * Deciding in render means React re-renders before it paints, so frame zero
 * of the desk already carries the attribute and `entering`.
 *
 * The attribute is cleared by a timer bound to the same numbers the CSS uses
 * (the last keyframe's `animationend` would do, but a timer cannot be lost to
 * a re-render mid-sequence).
 */
export function useEntrance(input: { mode: DeskMode; still: boolean; requested: boolean }): {
  enter: EnterPhase
} {
  const reduced = Boolean(useReducedMotion())
  const [enter, setEnter] = useState<EnterPhase>(null)
  const [seenMode, setSeenMode] = useState<DeskMode | null>(null)
  const { mode, still, requested } = input

  // State adjusted during render, the way the docs prescribe for "derived from
  // a prop that changed": `still` and `requested` are read at the moment the
  // mode changes and never after.
  if (seenMode !== mode) {
    setSeenMode(mode)
    let next: EnterPhase = null
    if (
      shouldPlayEntrance({ prevMode: seenMode, mode, reducedMotion: reduced, still, requested })
    ) {
      next = 'trading'
    } else if (seenMode === 'trading' && mode === 'chat' && !reduced) {
      next = 'chat'
    }
    if (next !== enter) setEnter(next)
  }

  useEffect(() => {
    if (!enter) return
    const timer = window.setTimeout(
      () => setEnter(null),
      enter === 'trading' ? ENTRANCE_MS + 40 : LEAVE_MS + 20,
    )
    return () => window.clearTimeout(timer)
  }, [enter])

  return { enter }
}
