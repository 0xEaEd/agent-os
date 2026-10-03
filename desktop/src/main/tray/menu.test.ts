// @vitest-environment node
import { describe, expect, it, vi } from 'vitest'
import type { MenuItemConstructorOptions } from 'electron'
import type { GatewayStatus } from '@shared/gateway'
import { DEFAULT_SETTINGS, mergeSettings, type DesktopSettings } from '@shared/settings'
import { EMPTY_TRAY_SUMMARY, TRAY_COUNT_MAX, type TraySummary } from '@shared/tray'
import { buildTrayMenu, gatewayLine, relativeIn, trayTitle, type TrayActions } from './menu'
import { sanitizeTraySummary } from './summary'

// menu.ts only imports electron's types; nothing of electron runs here.
vi.mock('electron', () => ({}))

const NOW = Date.parse('2026-10-03T12:00:00Z')
const RUNNING: GatewayStatus = {
  state: 'running',
  pid: 4242,
  url: 'http://127.0.0.1:18791',
  error: null,
}
const STOPPED: GatewayStatus = { state: 'stopped', pid: null, url: null, error: null }

function actions(): TrayActions & { [K in keyof TrayActions]: ReturnType<typeof vi.fn> } {
  return {
    openApp: vi.fn(),
    navigate: vi.fn(),
    openSettings: vi.fn(),
    startGateway: vi.fn(),
    stopGateway: vi.fn(),
    restartGateway: vi.fn(),
    quit: vi.fn(),
  }
}

function summary(patch: Partial<TraySummary> = {}): TraySummary {
  return { ...EMPTY_TRAY_SUMMARY, ...patch }
}

function external(): DesktopSettings {
  return mergeSettings(DEFAULT_SETTINGS, {
    gateway: { mode: 'external', host: '10.0.0.5', port: 9000 },
  })
}

function ids(items: MenuItemConstructorOptions[]): string[] {
  return items.map((i) => (i.type === 'separator' ? '---' : String(i.id)))
}

function item(items: MenuItemConstructorOptions[], id: string): MenuItemConstructorOptions {
  const found = items.find((i) => i.id === id)
  if (!found) throw new Error(`no item ${id} in ${ids(items).join(', ')}`)
  return found
}

/** Electron passes (menuItem, window, event); the handlers ignore all three. */
function click(entry: MenuItemConstructorOptions): void {
  ;(entry.click as unknown as () => void)()
}

describe('buildTrayMenu', () => {
  it('offers Quick Ask only while it is on and the shell can open it', () => {
    const a = { ...actions(), showQuickAsk: vi.fn() }
    const menu = buildTrayMenu(summary(), RUNNING, DEFAULT_SETTINGS, a, NOW)
    expect(ids(menu)).toContain('quick-ask')
    click(item(menu, 'quick-ask'))
    expect(a.showQuickAsk).toHaveBeenCalledTimes(1)
    // Settings › Behaviour › Quick Ask off: no row.
    const off = mergeSettings(DEFAULT_SETTINGS, { quickAsk: { enabled: false } })
    expect(ids(buildTrayMenu(summary(), RUNNING, off, a, NOW))).not.toContain('quick-ask')
    // A shell without Quick Ask: no row either.
    expect(ids(buildTrayMenu(summary(), RUNNING, DEFAULT_SETTINGS, actions(), NOW))).not.toContain(
      'quick-ask',
    )
  })

  it('is the bare menu when nothing is going on', () => {
    const menu = buildTrayMenu(summary(), RUNNING, DEFAULT_SETTINGS, actions(), NOW)
    expect(ids(menu)).toEqual([
      'gateway-status',
      '---',
      'open',
      'new-chat',
      '---',
      'gateway-stop',
      'settings',
      'quit',
    ])
    const status = item(menu, 'gateway-status')
    expect(status.label).toBe('●  Gateway running · 127.0.0.1:18791')
    expect(status.enabled).toBe(false)
  })

  it('lists approvals, the next DCA buy and live replies, each only when there is one', () => {
    const menu = buildTrayMenu(
      summary({
        liveTurns: 1,
        approvalsPending: 2,
        tradeApprovals: 1,
        nextMandate: { label: 'ETH → USDC', at: new Date(NOW + 14 * 60_000).toISOString() },
      }),
      RUNNING,
      DEFAULT_SETTINGS,
      actions(),
      NOW,
    )
    expect(ids(menu)).toEqual([
      'gateway-status',
      '---',
      'approvals',
      'next-dca',
      'live-turns',
      '---',
      'open',
      'new-chat',
      '---',
      'gateway-stop',
      'settings',
      'quit',
    ])
    expect(item(menu, 'approvals').label).toBe('2 approvals waiting')
    expect(item(menu, 'next-dca').label).toBe('Next DCA buy · ETH → USDC in 14 min')
    expect(item(menu, 'live-turns').label).toBe('1 reply in progress')
  })

  it('counts in the singular and plural', () => {
    const one = buildTrayMenu(
      summary({ approvalsPending: 1 }),
      RUNNING,
      DEFAULT_SETTINGS,
      actions(),
    )
    expect(item(one, 'approvals').label).toBe('1 approval waiting')
    const many = buildTrayMenu(summary({ liveTurns: 3 }), RUNNING, DEFAULT_SETTINGS, actions())
    expect(item(many, 'live-turns').label).toBe('3 replies in progress')
  })

  it('opens the desk for a desk order and only the window for a tool approval', () => {
    const a = actions()
    click(
      item(
        buildTrayMenu(
          summary({ approvalsPending: 3, tradeApprovals: 1 }),
          RUNNING,
          DEFAULT_SETTINGS,
          a,
        ),
        'approvals',
      ),
    )
    expect(a.navigate).toHaveBeenLastCalledWith({ type: 'trading' })
    click(
      item(
        buildTrayMenu(summary({ approvalsPending: 2 }), RUNNING, DEFAULT_SETTINGS, a),
        'approvals',
      ),
    )
    expect(a.navigate).toHaveBeenLastCalledWith({ type: 'approvals' })
  })

  it('wires every row to its action', () => {
    const a = actions()
    const menu = buildTrayMenu(
      summary({
        liveTurns: 1,
        nextMandate: { label: 'ETH → USDC', at: new Date(NOW + 60 * 60_000).toISOString() },
      }),
      RUNNING,
      DEFAULT_SETTINGS,
      a,
      NOW,
    )
    click(item(menu, 'next-dca'))
    expect(a.navigate).toHaveBeenLastCalledWith({ type: 'trading' })
    click(item(menu, 'live-turns'))
    click(item(menu, 'open'))
    expect(a.openApp).toHaveBeenCalledTimes(2)
    click(item(menu, 'new-chat'))
    expect(a.navigate).toHaveBeenLastCalledWith({ type: 'newChat' })
    click(item(menu, 'gateway-stop'))
    expect(a.stopGateway).toHaveBeenCalledOnce()
    click(item(menu, 'settings'))
    expect(a.openSettings).toHaveBeenCalledOnce()
    click(item(menu, 'quit'))
    expect(a.quit).toHaveBeenCalledOnce()
  })

  it('offers the one gateway action the state calls for (managed mode)', () => {
    const a = actions()
    const stopped = buildTrayMenu(summary(), STOPPED, DEFAULT_SETTINGS, a)
    expect(ids(stopped)).toContain('gateway-start')
    expect(ids(stopped)).not.toContain('gateway-stop')
    click(item(stopped, 'gateway-start'))
    expect(a.startGateway).toHaveBeenCalledOnce()

    const failed = buildTrayMenu(
      summary(),
      { ...STOPPED, state: 'error', error: 'port 18791 is in use' },
      DEFAULT_SETTINGS,
      a,
    )
    expect(ids(failed)).toContain('gateway-restart')
    click(item(failed, 'gateway-restart'))
    expect(a.restartGateway).toHaveBeenCalledOnce()

    for (const state of ['starting', 'stopping'] as const) {
      const busy = buildTrayMenu(summary(), { ...RUNNING, state }, DEFAULT_SETTINGS, a)
      const row = item(busy, `gateway-${state}`)
      expect(row.enabled).toBe(false)
      expect(ids(busy).filter((id) => id.startsWith('gateway-'))).toHaveLength(2)
    }
  })

  it('has no lifecycle rows for an external gateway but still says where it is', () => {
    for (const status of [RUNNING, STOPPED, { ...STOPPED, state: 'error' as const }]) {
      const menu = buildTrayMenu(summary(), status, external(), actions())
      expect(ids(menu).filter((id) => id.startsWith('gateway-'))).toEqual(['gateway-status'])
    }
    const stopped = buildTrayMenu(summary(), STOPPED, external(), actions())
    expect(item(stopped, 'gateway-status').label).toBe('○  Gateway stopped · 10.0.0.5:9000')
  })
})

describe('gatewayLine', () => {
  const gw = DEFAULT_SETTINGS.gateway

  it('names the state and the endpoint', () => {
    expect(gatewayLine(RUNNING, gw)).toBe('●  Gateway running · 127.0.0.1:18791')
    expect(gatewayLine({ ...RUNNING, state: 'starting', url: null }, gw)).toBe(
      '◌  Gateway starting · 127.0.0.1:18791',
    )
    expect(gatewayLine(STOPPED, gw)).toBe('○  Gateway stopped')
  })

  it('carries the first line of the error, cut short', () => {
    const long = `${'x'.repeat(80)}\nTraceback (most recent call last):`
    const line = gatewayLine({ ...STOPPED, state: 'error', error: long }, gw)
    expect(line.startsWith('✕  Gateway error · xxx')).toBe(true)
    expect(line).not.toContain('Traceback')
    expect(line.endsWith('…')).toBe(true)
    expect(line.length).toBeLessThan(70)
    expect(gatewayLine({ ...STOPPED, state: 'error', error: null }, gw)).toBe(
      '✕  Gateway error · 127.0.0.1:18791',
    )
  })
})

describe('relativeIn', () => {
  const at = (ms: number) => new Date(NOW + ms).toISOString()

  it('rounds down like the desk countdown', () => {
    expect(relativeIn(at(30_000), NOW)).toBe('in under a minute')
    expect(relativeIn(at(14 * 60_000 + 59_000), NOW)).toBe('in 14 min')
    expect(relativeIn(at(60 * 60_000), NOW)).toBe('in 1 h')
    expect(relativeIn(at(125 * 60_000), NOW)).toBe('in 2 h 5 min')
    expect(relativeIn(at(24 * 3_600_000), NOW)).toBe('in 1 d')
    expect(relativeIn(at(76 * 3_600_000), NOW)).toBe('in 3 d 4 h')
  })

  it('says "due now" once the moment has passed', () => {
    expect(relativeIn(at(0), NOW)).toBe('due now')
    expect(relativeIn(at(-5 * 60_000), NOW)).toBe('due now')
  })
})

describe('trayTitle', () => {
  it('is the approvals count, or nothing', () => {
    expect(trayTitle(summary())).toBe('')
    expect(trayTitle(summary({ approvalsPending: 2 }))).toBe('2')
    expect(trayTitle(summary({ approvalsPending: 120 }))).toBe('99+')
  })
})

describe('sanitizeTraySummary', () => {
  it('passes a well-formed summary through', () => {
    const at = '2026-10-03T12:14:00.000Z'
    expect(
      sanitizeTraySummary({
        liveTurns: 1,
        approvalsPending: 2,
        tradeApprovals: 1,
        nextMandate: { label: 'ETH → USDC', at },
      }),
    ).toEqual({
      liveTurns: 1,
      approvalsPending: 2,
      tradeApprovals: 1,
      nextMandate: { label: 'ETH → USDC', at },
    })
  })

  it('clamps counts to whole numbers in range', () => {
    const s = sanitizeTraySummary({
      liveTurns: -3,
      approvalsPending: 1e9,
      tradeApprovals: 2.7,
    })
    expect(s.liveTurns).toBe(0)
    expect(s.approvalsPending).toBe(TRAY_COUNT_MAX)
    expect(s.tradeApprovals).toBe(2)
    for (const bad of [NaN, Infinity, '7', null, undefined, {}]) {
      expect(sanitizeTraySummary({ liveTurns: bad }).liveTurns).toBe(0)
    }
  })

  it('never lets the desk part exceed the whole', () => {
    expect(sanitizeTraySummary({ approvalsPending: 1, tradeApprovals: 5 }).tradeApprovals).toBe(1)
  })

  it('cuts the mandate label to one short line', () => {
    const s = sanitizeTraySummary({
      nextMandate: {
        label: `ETH\n→\u0007 USDC ${'y'.repeat(200)}`,
        at: '2026-10-03T12:14:00Z',
      },
    })
    expect(s.nextMandate?.label.startsWith('ETH → USDC yyy')).toBe(true)
    expect(s.nextMandate?.label.length).toBeLessThanOrEqual(60)
    expect(s.nextMandate?.label.endsWith('…')).toBe(true)
    expect(s.nextMandate?.at).toBe('2026-10-03T12:14:00.000Z')
  })

  it('drops a mandate without a label or a real time', () => {
    for (const nextMandate of [
      { label: '', at: '2026-10-03T12:14:00Z' },
      { label: '   ', at: '2026-10-03T12:14:00Z' },
      { label: 'ETH → USDC', at: 'soon' },
      { label: 'ETH → USDC', at: 1_760_000_000_000 },
      { label: 42, at: '2026-10-03T12:14:00Z' },
      'ETH → USDC',
    ]) {
      expect(sanitizeTraySummary({ nextMandate }).nextMandate).toBeNull()
    }
  })

  it('reads garbage as nothing to say', () => {
    for (const raw of [null, undefined, 'summary', 42, []]) {
      expect(sanitizeTraySummary(raw)).toEqual(EMPTY_TRAY_SUMMARY)
    }
  })
})
