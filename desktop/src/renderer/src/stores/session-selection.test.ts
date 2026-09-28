import { beforeEach, describe, expect, it } from 'vitest'
import type { SessionRow } from './sessions'
import { rangeKeys, selectedRows, useSessionSelection, type ListSlot } from './session-selection'

function row(key: string): SessionRow {
  return { key, title: key, updatedAt: 0, live: false, raw: { key } }
}

function list(...keys: string[]): ListSlot[] {
  return keys.map((k) => ({ row: row(k), nested: false }))
}

const S = () => useSessionSelection.getState()

function keys(): string[] {
  return [...S().keys].sort()
}

describe('rangeKeys', () => {
  const order = ['a', 'b', 'c', 'd', 'e']

  it('takes both ends and everything between, either direction', () => {
    expect(rangeKeys(order, 'b', 'd')).toEqual(['b', 'c', 'd'])
    expect(rangeKeys(order, 'd', 'b')).toEqual(['b', 'c', 'd'])
    expect(rangeKeys(order, 'c', 'c')).toEqual(['c'])
  })

  it('falls back to the clicked row alone without an anchor on screen', () => {
    expect(rangeKeys(order, null, 'c')).toEqual(['c'])
    expect(rangeKeys(order, 'gone', 'c')).toEqual(['c'])
  })

  it('selects nothing for a row that is not on screen', () => {
    expect(rangeKeys(order, 'a', 'gone')).toEqual([])
  })
})

describe('useSessionSelection', () => {
  beforeEach(() => {
    useSessionSelection.setState({
      visible: [],
      order: [],
      keys: new Set(),
      anchor: null,
      pendingDelete: null,
    })
    S().setVisible(list('a', 'b', 'c', 'd', 'e'))
  })

  it('Cmd-click toggles a row in and out and moves the anchor', () => {
    S().toggle('b', false)
    S().toggle('d', false)
    expect(keys()).toEqual(['b', 'd'])
    S().toggle('b', false)
    expect(keys()).toEqual(['d'])
  })

  it('Shift-click selects from the anchor, and a second Shift-click re-pivots on it', () => {
    S().click('b', false)
    S().extend('d', false)
    expect(keys()).toEqual(['b', 'c', 'd'])
    S().extend('a', false)
    expect(keys()).toEqual(['a', 'b'])
  })

  it('Shift-click after Cmd-click ranges from the Cmd-clicked row', () => {
    S().toggle('e', false)
    S().extend('c', false)
    expect(keys()).toEqual(['c', 'd', 'e'])
  })

  it('ranges from the copy of a pinned filed chat that was clicked, not its first one', () => {
    // Folder: [p, f]. Pinned: [p, q]. Today: [a, b].
    S().setVisible([
      { row: row('p'), nested: true },
      { row: row('f'), nested: true },
      ...list('p', 'q', 'a', 'b'),
    ])
    S().click('b', false)
    S().extend('p', false)
    // The folder's f sits above the Pinned p on screen: not in the range.
    expect(keys()).toEqual(['a', 'b', 'p', 'q'])

    S().click('q', false)
    S().extend('f', true)
    // Between the folder's f and q sits the Pinned p.
    expect(keys()).toEqual(['f', 'p', 'q'])
    // Each key once, whichever copies it has.
    expect(selectedRows().map((r) => r.key)).toEqual(['p', 'f', 'q'])
  })

  it('a plain click clears the selection and becomes the anchor', () => {
    S().selectAll()
    expect(keys()).toHaveLength(5)
    S().click('c', false)
    expect(keys()).toEqual([])
    S().extend('d', false)
    expect(keys()).toEqual(['c', 'd'])
  })

  it('drops rows the list stops showing, so nothing hidden stays selected', () => {
    S().toggle('a', false)
    S().toggle('c', false)
    S().setVisible(list('b', 'c'))
    expect(keys()).toEqual(['c'])
    expect(S().anchor).not.toBeNull()
    S().setVisible(list('b'))
    expect(keys()).toEqual([])
    expect(S().anchor).toBeNull()
  })

  it('keeps the same set when a refresh removes nothing, so rows do not re-render', () => {
    S().toggle('a', false)
    const before = S().keys
    S().setVisible(list('a', 'b', 'c', 'd', 'e'))
    expect(S().keys).toBe(before)
  })

  it('selects only rows on screen, and lists them in screen order', () => {
    S().select(['d', 'gone', 'a'])
    expect(keys()).toEqual(['a', 'd'])
    expect(selectedRows().map((r) => r.key)).toEqual(['a', 'd'])
  })
})
