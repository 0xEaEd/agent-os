import type { MenuItemConstructorOptions } from 'electron'
import type { GatewayStatus } from '@shared/gateway'
import type { NotifyTarget } from '@shared/notify'
import type { DesktopSettings, GatewaySettings } from '@shared/settings'
import type { TraySummary } from '@shared/tray'
import { oneLine } from './summary'

/**
 * What a menu row does. Injected so the menu is a pure function of its
 * inputs; `tray.ts` binds these to the same doors the app menu and the
 * notifications use (focus or create the window, `requestOpenSettings`,
 * the supervisor, `app.quit`).
 */
export interface TrayActions {
  /** Bring the main window forward, creating it when none is open. */
  openApp(): void
  /** Open the window and hand the renderer a target (the notifications' router). */
  navigate(target: NotifyTarget): void
  openSettings(): void
  /** Open the Quick Ask panel; absent when the shell has no Quick Ask. */
  showQuickAsk?(): void
  startGateway(): void
  stopGateway(): void
  restartGateway(): void
  /** `app.quit()`, so `before-quit` stops a managed gateway as ⌘Q does. */
  quit(): void
}

/** The error text on the status line; the full one is in Settings › Gateway. */
const ERROR_MAX = 48

/** "127.0.0.1:18791" from the running URL, or from settings when there is none. */
export function endpointText(status: GatewayStatus, gateway: GatewaySettings): string {
  if (status.url) {
    try {
      return new URL(status.url).host
    } catch {
      // Fall through to the configured endpoint.
    }
  }
  return `${gateway.host}:${gateway.port}`
}

/** The first, disabled row: the gateway's state, where it is, or why it failed. */
export function gatewayLine(status: GatewayStatus, gateway: GatewaySettings): string {
  const at = endpointText(status, gateway)
  switch (status.state) {
    case 'running':
      return `●  Gateway running · ${at}`
    case 'starting':
      return `◌  Gateway starting · ${at}`
    case 'stopping':
      return `◌  Gateway stopping · ${at}`
    case 'error': {
      const why = oneLine((status.error ?? '').split('\n')[0], ERROR_MAX)
      return why ? `✕  Gateway error · ${why}` : `✕  Gateway error · ${at}`
    }
    case 'stopped':
    default:
      // An external gateway is somewhere else; say where it was looked for.
      return gateway.mode === 'external' ? `○  Gateway stopped · ${at}` : '○  Gateway stopped'
  }
}

/**
 * "in 14 min", "in 2 h 5 min", "in 3 d 4 h", or "due now" once the moment
 * has passed (the engine fires it on its next tick). Rounded down like the
 * desk's countdown, so the menu never says a minute more than the desk.
 */
export function relativeIn(at: string, now: number): string {
  const ms = Date.parse(at) - now
  if (!Number.isFinite(ms) || ms <= 0) return 'due now'
  if (ms < 60_000) return 'in under a minute'
  const minutes = Math.floor(ms / 60_000)
  if (minutes < 60) return `in ${minutes} min`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return minutes % 60 ? `in ${hours} h ${minutes % 60} min` : `in ${hours} h`
  const days = Math.floor(hours / 24)
  return hours % 24 ? `in ${days} d ${hours % 24} h` : `in ${days} d`
}

/** Beside the icon: the approvals waiting, or nothing. */
export function trayTitle(summary: TraySummary): string {
  const n = summary.approvalsPending
  if (n <= 0) return ''
  return n > 99 ? '99+' : String(n)
}

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`
}

/** The managed gateway's one lifecycle row, by state. External mode has none. */
function gatewayAction(
  status: GatewayStatus,
  actions: TrayActions,
): MenuItemConstructorOptions | null {
  switch (status.state) {
    case 'running':
      return { id: 'gateway-stop', label: 'Stop Gateway', click: () => actions.stopGateway() }
    case 'error':
      return {
        id: 'gateway-restart',
        label: 'Restart Gateway',
        click: () => actions.restartGateway(),
      }
    case 'starting':
      return { id: 'gateway-starting', label: 'Starting Gateway…', enabled: false }
    case 'stopping':
      return { id: 'gateway-stopping', label: 'Stopping Gateway…', enabled: false }
    case 'stopped':
    default:
      return { id: 'gateway-start', label: 'Start Gateway', click: () => actions.startGateway() }
  }
}

/**
 * The menu under the status item, from what the renderer last reported, the
 * supervisor's status and the settings. Pure: no Electron at runtime, so it
 * is unit-tested in the node environment.
 */
export function buildTrayMenu(
  summary: TraySummary,
  status: GatewayStatus,
  settings: DesktopSettings,
  actions: TrayActions,
  now: number = Date.now(),
): MenuItemConstructorOptions[] {
  const sep: MenuItemConstructorOptions = { type: 'separator' }
  const items: MenuItemConstructorOptions[] = [
    { id: 'gateway-status', label: gatewayLine(status, settings.gateway), enabled: false },
    sep,
  ]

  // What is going on: each row only while there is something to say.
  const activity: MenuItemConstructorOptions[] = []
  if (summary.approvalsPending > 0) {
    // A desk order opens the desk; a tool approval is a prompt the window
    // shows by itself, so bringing it forward is all a click has to do.
    const target: NotifyTarget =
      summary.tradeApprovals > 0 ? { type: 'trading' } : { type: 'approvals' }
    activity.push({
      id: 'approvals',
      label: `${plural(summary.approvalsPending, 'approval', 'approvals')} waiting`,
      click: () => actions.navigate(target),
    })
  }
  if (summary.nextMandate) {
    const { label, at } = summary.nextMandate
    activity.push({
      id: 'next-dca',
      label: `Next DCA buy · ${label} ${relativeIn(at, now)}`,
      click: () => actions.navigate({ type: 'trading' }),
    })
  }
  if (summary.liveTurns > 0) {
    activity.push({
      id: 'live-turns',
      label: `${plural(summary.liveTurns, 'reply', 'replies')} in progress`,
      click: () => actions.openApp(),
    })
  }
  if (activity.length > 0) items.push(...activity, sep)

  items.push(
    { id: 'open', label: 'Open AgentOS', click: () => actions.openApp() },
    { id: 'new-chat', label: 'New Chat', click: () => actions.navigate({ type: 'newChat' }) },
  )
  // Only while Quick Ask is on: the row opens the same panel the key does,
  // so a refused key still leaves a way in.
  const showQuickAsk = actions.showQuickAsk
  if (settings.quickAsk.enabled && showQuickAsk) {
    items.push({ id: 'quick-ask', label: 'Quick Ask…', click: () => showQuickAsk() })
  }
  items.push(sep)

  const lifecycle = settings.gateway.mode === 'managed' ? gatewayAction(status, actions) : null
  if (lifecycle) items.push(lifecycle)
  items.push(
    { id: 'settings', label: 'Settings…', click: () => actions.openSettings() },
    { id: 'quit', label: 'Quit AgentOS', click: () => actions.quit() },
  )
  return items
}
