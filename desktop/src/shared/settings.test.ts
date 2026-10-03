import { describe, expect, it } from 'vitest'
import { DEFAULT_NOTIFICATION_SETTINGS, mergeSettings, normalizeSettings } from './settings'

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

describe('general settings', () => {
  it('shows the menu bar item unless the file says otherwise', () => {
    expect(normalizeSettings({}).general.showInMenuBar).toBe(true)
    // A file from before the setting existed keeps the item.
    expect(normalizeSettings({ general: { enterToSend: false } }).general.showInMenuBar).toBe(true)
    expect(normalizeSettings({ general: { showInMenuBar: 'no' } }).general.showInMenuBar).toBe(true)
    expect(normalizeSettings({ general: { showInMenuBar: false } }).general.showInMenuBar).toBe(
      false,
    )
  })

  it('turns the menu bar item off and on through a patch', () => {
    const off = mergeSettings(normalizeSettings({}), { general: { showInMenuBar: false } })
    expect(off.general.showInMenuBar).toBe(false)
    expect(off.general.stopGatewayOnQuit).toBe(true)
    expect(mergeSettings(off, { general: { showInMenuBar: true } }).general.showInMenuBar).toBe(
      true,
    )
  })
})
