import { act, render, renderHook } from '@testing-library/react'
import { useLayoutEffect } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ENTRANCE_MS } from './mode-logic'
import { useEntrance } from './useEntrance'

const reduced = vi.hoisted(() => ({ value: false }))
vi.mock('motion/react', async () => {
  const actual = await vi.importActual<typeof import('motion/react')>('motion/react')
  return { ...actual, useReducedMotion: () => reduced.value }
})

describe('useEntrance', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    reduced.value = false
  })
  afterEach(() => vi.useRealTimers())

  it('sets data-enter=trading on a switch from chat and clears it after the choreography', () => {
    const { result, rerender } = renderHook(
      (p: { mode: 'chat' | 'trading' }) =>
        useEntrance({ mode: p.mode, still: false, requested: false }),
      { initialProps: { mode: 'chat' as 'chat' | 'trading' } },
    )
    expect(result.current.enter).toBeNull()
    rerender({ mode: 'trading' })
    expect(result.current.enter).toBe('trading')
    act(() => vi.advanceTimersByTime(ENTRANCE_MS + 100))
    expect(result.current.enter).toBeNull()
    // Re-rendering in trading mode does not replay it.
    rerender({ mode: 'trading' })
    expect(result.current.enter).toBeNull()
  })

  it('plays on a fresh mount only when the desk was requested', () => {
    const quiet = renderHook(() => useEntrance({ mode: 'trading', still: false, requested: false }))
    expect(quiet.result.current.enter).toBeNull()
    const asked = renderHook(() => useEntrance({ mode: 'trading', still: false, requested: true }))
    expect(asked.result.current.enter).toBe('trading')
  })

  it('skips the choreography under reduced motion and while an ask is pending', () => {
    reduced.value = true
    const r1 = renderHook(
      (p: { mode: 'chat' | 'trading' }) =>
        useEntrance({ mode: p.mode, still: false, requested: false }),
      { initialProps: { mode: 'chat' as 'chat' | 'trading' } },
    )
    r1.rerender({ mode: 'trading' })
    expect(r1.result.current.enter).toBeNull()
    reduced.value = false
    const r2 = renderHook(
      (p: { mode: 'chat' | 'trading' }) =>
        useEntrance({ mode: p.mode, still: true, requested: false }),
      { initialProps: { mode: 'chat' as 'chat' | 'trading' } },
    )
    r2.rerender({ mode: 'trading' })
    expect(r2.result.current.enter).toBeNull()
  })

  /* The bug this pins: the phase used to be chosen in a useEffect, which runs
     after the browser paints. The desk's first frame therefore had no
     `data-enter`, so the instruments painted at rest and were yanked back to
     their from-state on the frame after — and ChatView got `entering` a
     commit late, so the composer's centre-to-bottom spring was already
     flying. Both are only visible coming from a fresh, undocked chat, which
     is exactly where the switch was reported as janky. */
  it('has the phase before the first paint of the switch, not a frame later', () => {
    const paints: (string | null)[] = []
    function Probe({ mode }: { mode: 'chat' | 'trading' }) {
      const { enter } = useEntrance({ mode, still: false, requested: false })
      // Layout effects run after the DOM is written and before paint, so this
      // records exactly what the user's first frame of the desk shows.
      useLayoutEffect(() => {
        paints.push(enter)
      })
      return <div data-enter={enter ?? undefined} />
    }
    const { rerender } = render(<Probe mode="chat" />)
    paints.length = 0
    rerender(<Probe mode="trading" />)
    expect(paints[0]).toBe('trading')
  })

  it('plays the short reverse when leaving', () => {
    const r = renderHook(
      (p: { mode: 'chat' | 'trading' }) =>
        useEntrance({ mode: p.mode, still: false, requested: true }),
      { initialProps: { mode: 'trading' as 'chat' | 'trading' } },
    )
    act(() => vi.advanceTimersByTime(ENTRANCE_MS + 100))
    r.rerender({ mode: 'chat' })
    expect(r.result.current.enter).toBe('chat')
    act(() => vi.advanceTimersByTime(400))
    expect(r.result.current.enter).toBeNull()
  })
})
