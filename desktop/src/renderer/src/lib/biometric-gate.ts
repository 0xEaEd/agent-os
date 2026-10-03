import { toast } from 'sonner'
import { create } from 'zustand'
import { AUTH_REASON_MAX, type AuthResult } from '@shared/app'
import type { TouchIdMode } from '@shared/settings'
import { t } from '~/i18n'
import { desktopApi } from '~/lib/desktop-api'
import { useSettings } from '~/stores/settings'

/**
 * Touch ID in front of the operator's money-moving clicks (Settings ›
 * Security). One door, used by every path that approves an order, approves
 * a DCA mandate, exports a private key or removes a wallet: it sits in front
 * of the RPC, so a cancelled or failed prompt sends nothing at all.
 *
 * - `approve-high`: a high-risk approval (the card's "risk-high" stamp).
 * - `approve`: any other approval.
 * - `vault`: a private-key export or a wallet removal, whatever the risk.
 */
export type GateKind = 'approve-high' | 'approve' | 'vault'

/** Whether `mode` asks for a fingerprint before `kind`. */
export function needsTouchId(mode: TouchIdMode, kind: GateKind): boolean {
  if (mode === 'off') return false
  if (mode === 'all') return true
  return kind !== 'approve'
}

/** The reason as the system sheet may show it: trimmed, at most 120 characters. */
export function clampReason(reason: string): string {
  const clean = reason.replace(/\s+/g, ' ').trim()
  return clean.length <= AUTH_REASON_MAX ? clean : `${clean.slice(0, AUTH_REASON_MAX - 1)}…`
}

/**
 * What the prompt on screen is for, so the button that asked can say
 * "Touch ID…" while the sheet is up. Null when no prompt is up.
 */
export const useTouchIdPrompt = create<{ key: string | null }>(() => ({ key: null }))

const TOAST_ID = 'touch-id'

function toastFailure(kind: GateKind, result: Exclude<AuthResult, { ok: true }>): void {
  switch (result.reason) {
    case 'cancelled':
      toast.info(
        kind === 'vault' ? t('trading.touchId.cancelled.vault') : t('trading.touchId.cancelled'),
        { id: TOAST_ID },
      )
      return
    case 'unavailable':
      toast.error(t('trading.touchId.unavailable'), { id: TOAST_ID })
      return
    default:
      toast.error(t('trading.touchId.failed'), { id: TOAST_ID })
  }
}

let inFlight = false

/**
 * Resolve true when the action may proceed. Reads the setting at call time;
 * `off` never prompts. A cancelled, unavailable or failed prompt toasts and
 * resolves false — nothing falls through to the action, and the setting
 * stays editable without Touch ID so nobody is locked out. A second call
 * while a prompt is up resolves false quietly: the sheet is modal, the
 * click that opened it is the one that counts.
 *
 * `key` names what is asking (an order id, a mandate id, a wallet) for
 * `useTouchIdPrompt`.
 */
export async function biometricGate(
  kind: GateKind,
  reason: string,
  key: string | null = null,
): Promise<boolean> {
  // Settings arrive with the gateway providers; before that the store holds
  // the defaults, and "off" must not be read off a store that never loaded.
  if (!useSettings.getState().loaded) {
    try {
      await useSettings.getState().load()
    } catch {
      /* the bridge could not read them: the defaults stand */
    }
  }
  const mode = useSettings.getState().settings.security.touchId
  if (!needsTouchId(mode, kind)) return true
  if (inFlight) return false
  inFlight = true
  useTouchIdPrompt.setState({ key })
  let result: AuthResult
  try {
    result = await desktopApi().app.authenticate(clampReason(reason))
  } catch {
    result = { ok: false, reason: 'failed' }
  } finally {
    inFlight = false
    useTouchIdPrompt.setState({ key: null })
  }
  if (result.ok) return true
  toastFailure(kind, result)
  return false
}

/** Thrown by `requireBiometric`: the gate said no and has already toasted why. */
export class TouchIdDeclined extends Error {
  constructor() {
    super('Touch ID did not confirm this action.')
    this.name = 'TouchIdDeclined'
  }
}

export function isTouchIdDeclined(error: unknown): boolean {
  return error instanceof TouchIdDeclined
}

/**
 * `biometricGate` for a mutation function: resolves when the action may
 * proceed, throws `TouchIdDeclined` (already toasted) when it may not.
 */
export async function requireBiometric(
  kind: GateKind,
  reason: string,
  key: string | null = null,
): Promise<void> {
  if (!(await biometricGate(kind, reason, key))) throw new TouchIdDeclined()
}

/** Test hook: forget a prompt left in flight by a test that never settled it. */
export function resetBiometricGateForTests(): void {
  inFlight = false
  useTouchIdPrompt.setState({ key: null })
}
