import { describe, expect, it } from 'vitest'
import {
  DEFAULT_NOTIFICATION_SETTINGS,
  DEFAULT_QUICK_ASK_SETTINGS,
  mergeSettings,
  normalizeSettings,
} from './settings'

describe('notification settings', () => {
  it('reads a legacy file with only the three old keys', () => {
    const s = normalizeSettings({
      notifications: { sound: false, replyDone: false, approvals: true },
    })
    expect(s.notifications).toEqual({
      ...DEFAULT_NOTIFICATION_SETTINGS,
      sound: false,
      replyDone: false,
      approvals: true,
    })
  })

  it('rejects values outside the enums and keeps the defaults', () => {
    const s = normalizeSettings({
      notifications: {
        whenActive: 'always',
        jobs: 'some',
        replyMinSeconds: 7,
        soundName: 'Airhorn',
        muteUntil: 'soon',
      },
    })
    expect(s.notifications.whenActive).toBe('banner')
    expect(s.notifications.jobs).toBe('failures')
    expect(s.notifications.replyMinSeconds).toBe(0)
    expect(s.notifications.soundName).toBe('chime')
    expect(s.notifications.muteUntil).toBeNull()
  })

  it('accepts every valid value', () => {
    const s = normalizeSettings({
      notifications: {
        enabled: false,
        whenActive: 'system',
        jobs: 'all',
        replyMinSeconds: 60,
        soundName: 'Glass',
        muteUntil: 1_800_000_000_000,
        badge: false,
        bounce: false,
        preview: false,
      },
    })
    expect(s.notifications).toMatchObject({
      enabled: false,
      whenActive: 'system',
      jobs: 'all',
      replyMinSeconds: 60,
      soundName: 'Glass',
      muteUntil: 1_800_000_000_000,
      badge: false,
      bounce: false,
      preview: false,
    })
  })

  it('patches one key without touching the rest', () => {
    const base = normalizeSettings({})
    const next = mergeSettings(base, { notifications: { muteUntil: 42 } })
    expect(next.notifications.muteUntil).toBe(42)
    expect(next.notifications.soundName).toBe('chime')
    expect(
      mergeSettings(next, { notifications: { muteUntil: null } }).notifications.muteUntil,
    ).toBe(null)
  })
})

describe('quick ask settings', () => {
  it('defaults to on, on ⌥ Space, for a file written before the section existed', () => {
    const s = normalizeSettings({ general: { enterToSend: false } })
    expect(s.quickAsk).toEqual(DEFAULT_QUICK_ASK_SETTINGS)
    expect(s.quickAsk).toEqual({ enabled: true, shortcut: 'Alt+Space' })
  })

  it('keeps a valid choice from the fixed list', () => {
    const s = normalizeSettings({
      quickAsk: { enabled: false, shortcut: 'CommandOrControl+Shift+Space' },
    })
    expect(s.quickAsk).toEqual({ enabled: false, shortcut: 'CommandOrControl+Shift+Space' })
  })

  it('refuses a free-form accelerator or a non-boolean switch', () => {
    const s = normalizeSettings({
      quickAsk: { enabled: 'yes', shortcut: 'Command+Q', extra: 1 },
    })
    expect(s.quickAsk).toEqual(DEFAULT_QUICK_ASK_SETTINGS)
    expect(normalizeSettings({ quickAsk: 'Alt+Space' }).quickAsk).toEqual(
      DEFAULT_QUICK_ASK_SETTINGS,
    )
  })

  it('merges a one-key patch without touching the rest of the section', () => {
    const base = normalizeSettings({ quickAsk: { enabled: true, shortcut: 'Control+Space' } })
    const next = mergeSettings(base, { quickAsk: { enabled: false } })
    expect(next.quickAsk).toEqual({ enabled: false, shortcut: 'Control+Space' })
    expect(
      mergeSettings(next, { quickAsk: { shortcut: 'Bogus' as never } }).quickAsk.shortcut,
    ).toBe('Alt+Space')
  })
})
