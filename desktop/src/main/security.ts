import { systemPreferences } from 'electron'
import { AUTH_REASON_MAX, type AuthFailure, type AuthResult } from '@shared/app'

/**
 * Touch ID for the operator's money-moving clicks (Settings › Security).
 * `promptTouchID` evaluates LocalAuthentication's biometrics-only policy, so
 * there is no password fallback: the point is a fingerprint.
 */

/** The two calls this module needs; Electron's `systemPreferences` in the app. */
export interface TouchIdBackend {
  canPromptTouchID?: () => boolean
  promptTouchID?: (reason: string) => Promise<void>
}

/**
 * Why a rejected `promptTouchID` did not confirm the user, from the message
 * LocalAuthentication gave ("Canceled by user.", "No fingers are enrolled
 * with Touch ID.", "Biometry is locked out." …). Matched case-insensitively
 * on substrings; anything unrecognised is a plain failure.
 */
export function authFailureReason(error: unknown): AuthFailure {
  const message = (
    error instanceof Error ? error.message : typeof error === 'string' ? error : ''
  ).toLowerCase()
  if (!message) return 'failed'
  // The user (or the system, e.g. another app took focus) dismissed the
  // sheet, or chose the fallback button: nothing about who is at the keyboard.
  if (/cancel|fallback/.test(message)) return 'cancelled'
  // The sheet could not be put up or the sensor cannot be used right now.
  if (
    /not available|unavailable|not enrolled|no fingers|no identities|not set|locked ?out|interaction is required|not interactive/.test(
      message,
    )
  )
    return 'unavailable'
  return 'failed'
}

/** A reason the system sheet may show: a non-blank string of at most 120 characters. */
export function isValidAuthReason(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0 && value.length <= AUTH_REASON_MAX
}

/** Whether Touch ID can be asked for now (false with no sensor, none enrolled, lid closed). */
export function biometricsAvailable(backend: TouchIdBackend = systemPreferences): boolean {
  try {
    return typeof backend.canPromptTouchID === 'function' && backend.canPromptTouchID()
  } catch {
    return false
  }
}

let inFlight = false

/**
 * One Touch ID prompt. A second request while the sheet is up is refused as
 * `cancelled` rather than queued: the renderer already ignores a second
 * click, and two stacked system sheets would be one too many.
 */
export async function authenticate(
  reason: unknown,
  backend: TouchIdBackend = systemPreferences,
): Promise<AuthResult> {
  if (!isValidAuthReason(reason)) return { ok: false, reason: 'failed' }
  if (!biometricsAvailable(backend) || typeof backend.promptTouchID !== 'function')
    return { ok: false, reason: 'unavailable' }
  if (inFlight) return { ok: false, reason: 'cancelled' }
  inFlight = true
  try {
    await backend.promptTouchID(reason.trim())
    return { ok: true }
  } catch (error) {
    return { ok: false, reason: authFailureReason(error) }
  } finally {
    inFlight = false
  }
}
