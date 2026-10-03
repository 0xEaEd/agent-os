/**
 * The macOS menu bar item (`main/tray`). Main never talks to the gateway, so
 * the renderer pushes what the menu needs to say about the gateway's work
 * (`tray:summary`); main merges it with the supervisor's status and the
 * settings. Clicks that need the app land back in the renderer as a
 * `NotifyTarget` over `tray:navigate`, through the notifications' router.
 */

/** The DCA buy that fires soonest, among the active mandates. */
export interface TrayNextMandate {
  /** What it buys, as the desk says it: "ETH → USDC". */
  label: string
  /** ISO time of the buy (`schedule.nextRunAt`). */
  at: string
}

export interface TraySummary {
  /** Sessions with a turn streaming right now. */
  liveTurns: number
  /** Everything waiting on the user: tool approvals plus desk orders. */
  approvalsPending: number
  /**
   * The part of `approvalsPending` that sits on the trading desk (orders
   * awaiting approval). When non-zero the approvals row opens the desk;
   * otherwise it only brings the window forward, where the tool approval
   * prompt shows itself.
   */
  tradeApprovals: number
  nextMandate: TrayNextMandate | null
}

export const EMPTY_TRAY_SUMMARY: TraySummary = {
  liveTurns: 0,
  approvalsPending: 0,
  tradeApprovals: 0,
  nextMandate: null,
}

/** Counts above this read "999+" anyway; it also caps what main accepts. */
export const TRAY_COUNT_MAX = 999
/** The mandate label as shown in a menu row; longer is cut. */
export const TRAY_LABEL_MAX = 60
