// @vitest-environment node
import { describe, expect, it, vi } from 'vitest'

// Only the sanitizers run here; electron and the notifier are never called.
vi.mock('electron', () => ({
  app: {},
  BrowserWindow: class {},
  Notification: { isSupported: () => false },
  shell: {},
  ipcMain: { handle: vi.fn() },
}))
vi.mock('../notify/notifier', () => ({
  bounceDock: vi.fn(),
  notificationsSupported: vi.fn(),
  openNotificationSettings: vi.fn(),
  playSystemSound: vi.fn(),
  setDockBadge: vi.fn(),
  showNotification: vi.fn(),
}))

import { sanitizeRequest, sanitizeTarget } from './notify'

describe('sanitizeTarget', () => {
  it('keeps a trading target, with its order when named (#3600)', () => {
    expect(sanitizeTarget({ type: 'trading', orderId: 'ord_1' })).toEqual({
      type: 'trading',
      orderId: 'ord_1',
    })
    expect(sanitizeTarget({ type: 'trading' })).toEqual({ type: 'trading' })
    expect(sanitizeTarget({ type: 'trading', orderId: 42 })).toEqual({ type: 'trading' })
  })

  it('bounds the order id like the other ids', () => {
    const long = 'x'.repeat(200)
    const out = sanitizeTarget({ type: 'trading', orderId: long })
    expect(out.type).toBe('trading')
    expect(out.type === 'trading' && out.orderId?.length).toBe(128)
  })

  it('still maps an unknown shape to none', () => {
    expect(sanitizeTarget({ type: 'desk' })).toEqual({ type: 'none' })
    expect(sanitizeTarget(null)).toEqual({ type: 'none' })
    expect(sanitizeTarget({ type: 'session', key: '' })).toEqual({ type: 'none' })
  })
})

describe('sanitizeRequest', () => {
  it('keeps the trade kinds instead of folding them into test', () => {
    const ok = sanitizeRequest({ kind: 'trade', title: 'Swap confirmed' })
    expect(ok?.kind).toBe('trade')
    expect(ok?.tag).toBe('trade')
    expect(sanitizeRequest({ kind: 'tradeFailed', title: 'Swap failed' })?.kind).toBe('tradeFailed')
    expect(sanitizeRequest({ kind: 'bogus', title: 'x' })?.kind).toBe('test')
  })
})
