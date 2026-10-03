import { beforeEach, describe, expect, it } from 'vitest'
import { desktopApi, resetDesktopApiForTests } from '~/lib/desktop-api'
import { bindQuickAskDelivery, quickAskDestination, useQuickAsk } from './quick-ask'

const KEY = 'agent:main:webchat:abc123'

beforeEach(() => {
  delete window.agentos
  localStorage.clear()
  resetDesktopApiForTests()
  useQuickAsk.setState({ queue: [] })
})

describe('quickAskDestination', () => {
  it('sends a new-chat submission to the keyless home, wherever the window is', () => {
    expect(quickAskDestination('new', `/sessions/${encodeURIComponent(KEY)}`, KEY)).toBe('')
    expect(quickAskDestination('new', '/projects/p1', KEY)).toBe('')
  })

  it('sends a current-chat submission to the session on screen', () => {
    expect(
      quickAskDestination('current', `/sessions/${encodeURIComponent(KEY)}`, 'agent:main:other'),
    ).toBe(KEY)
  })

  it('falls back to the last session off a chat, and to a fresh one with none', () => {
    expect(quickAskDestination('current', '/sessions', KEY)).toBe(KEY)
    expect(quickAskDestination('current', '/projects/p1', KEY)).toBe(KEY)
    expect(quickAskDestination('current', '/sessions', null)).toBe('')
  })

  it('returns the canonical key, the one the chat route compares against', () => {
    expect(quickAskDestination('current', '/sessions/sess-xyz', null)).toBe(
      'agent:main:webchat:xyz',
    )
  })
})

describe('useQuickAsk', () => {
  it('queues, routes and drops by id, oldest first', () => {
    const s = useQuickAsk.getState()
    s.receive([
      { text: 'one', target: 'new' },
      { text: 'two', target: 'current' },
    ])
    const [first, second] = useQuickAsk.getState().queue
    expect(first).toMatchObject({ text: 'one', target: 'new' })
    expect(first!.dest).toBeUndefined()
    expect(second).toMatchObject({ text: 'two', target: 'current' })
    expect(first!.id).not.toBe(second!.id)

    useQuickAsk.getState().route(first!.id, '')
    expect(useQuickAsk.getState().queue[0]!.dest).toBe('')
    useQuickAsk.getState().done(first!.id)
    expect(useQuickAsk.getState().queue.map((i) => i.text)).toEqual(['two'])
  })
})

describe('bindQuickAskDelivery', () => {
  it('collects what was waiting before it bound, then each new delivery', async () => {
    const api = desktopApi()
    // Submitted while the main window was still being created.
    await api.quickAsk.submit({ text: 'early', target: 'new' })
    const off = bindQuickAskDelivery()
    await Promise.resolve()
    await Promise.resolve()
    expect(useQuickAsk.getState().queue.map((i) => i.text)).toEqual(['early'])

    await api.quickAsk.submit({ text: 'later', target: 'current' })
    await Promise.resolve()
    await Promise.resolve()
    expect(useQuickAsk.getState().queue.map((i) => [i.text, i.target])).toEqual([
      ['early', 'new'],
      ['later', 'current'],
    ])
    off()
  })

  it('never collects the same submission twice', async () => {
    const api = desktopApi()
    await api.quickAsk.submit({ text: 'once', target: 'new' })
    const offA = bindQuickAskDelivery()
    const offB = bindQuickAskDelivery()
    await Promise.resolve()
    await Promise.resolve()
    expect(useQuickAsk.getState().queue).toHaveLength(1)
    offA()
    offB()
  })
})
