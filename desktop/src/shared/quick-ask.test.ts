import { describe, expect, it } from 'vitest'
import {
  clampPanelHeight,
  isQuickAskShortcut,
  parseQuickAskSubmission,
  QUICK_ASK_KEYCAPS,
  QUICK_ASK_MAX_BYTES,
  QUICK_ASK_MAX_HEIGHT,
  QUICK_ASK_MIN_HEIGHT,
  QUICK_ASK_SHORTCUTS,
} from './quick-ask'

describe('parseQuickAskSubmission', () => {
  it('accepts text for a new or the current session, trimmed', () => {
    expect(parseQuickAskSubmission({ text: '  what is a vault?\n', target: 'new' })).toEqual({
      text: 'what is a vault?',
      target: 'new',
    })
    expect(parseQuickAskSubmission({ text: 'and then?', target: 'current' })).toEqual({
      text: 'and then?',
      target: 'current',
    })
  })

  it('refuses a payload that is not an object with string text', () => {
    for (const raw of [
      null,
      undefined,
      'hello',
      42,
      [],
      { target: 'new' },
      { text: 42, target: 'new' },
      { text: ['hi'], target: 'new' },
      { text: { toString: () => 'hi' }, target: 'new' },
      { text: null, target: 'current' },
    ]) {
      expect(parseQuickAskSubmission(raw), JSON.stringify(raw) ?? String(raw)).toBeNull()
    }
  })

  it('refuses an unknown target', () => {
    expect(parseQuickAskSubmission({ text: 'hi' })).toBeNull()
    expect(parseQuickAskSubmission({ text: 'hi', target: 'other' })).toBeNull()
    expect(parseQuickAskSubmission({ text: 'hi', target: 'NEW' })).toBeNull()
  })

  it('refuses empty and whitespace-only text', () => {
    expect(parseQuickAskSubmission({ text: '', target: 'new' })).toBeNull()
    expect(parseQuickAskSubmission({ text: ' \n\t  ', target: 'new' })).toBeNull()
  })

  it('refuses text over 20 kB, measured in UTF-8 bytes', () => {
    expect(QUICK_ASK_MAX_BYTES).toBe(20_000)
    const atLimit = 'a'.repeat(QUICK_ASK_MAX_BYTES)
    expect(parseQuickAskSubmission({ text: atLimit, target: 'new' })?.text).toBe(atLimit)
    expect(parseQuickAskSubmission({ text: `${atLimit}a`, target: 'new' })).toBeNull()
    // 7 000 characters, 21 000 bytes: a length check in UTF-16 units would let it through.
    expect(parseQuickAskSubmission({ text: '日'.repeat(7_000), target: 'new' })).toBeNull()
  })
})

describe('shortcuts', () => {
  it('knows exactly the fixed list, each with keycaps', () => {
    expect(QUICK_ASK_SHORTCUTS).toEqual([
      'Alt+Space',
      'Control+Space',
      'CommandOrControl+Shift+Space',
    ])
    for (const s of QUICK_ASK_SHORTCUTS) {
      expect(isQuickAskShortcut(s)).toBe(true)
      expect(QUICK_ASK_KEYCAPS[s].length).toBeGreaterThan(1)
    }
    expect(isQuickAskShortcut('Command+Q')).toBe(false)
    expect(isQuickAskShortcut(undefined)).toBe(false)
  })
})

describe('clampPanelHeight', () => {
  it('clamps into the panel range and rounds', () => {
    expect(clampPanelHeight(118.4)).toBe(118)
    expect(clampPanelHeight(0)).toBe(QUICK_ASK_MIN_HEIGHT)
    expect(clampPanelHeight(10_000)).toBe(QUICK_ASK_MAX_HEIGHT)
  })

  it('refuses what is not a finite number', () => {
    expect(clampPanelHeight('120')).toBeNull()
    expect(clampPanelHeight(Number.NaN)).toBeNull()
    expect(clampPanelHeight(Infinity)).toBeNull()
    expect(clampPanelHeight(undefined)).toBeNull()
  })
})
