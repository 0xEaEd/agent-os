import type { QuickAskSubmission } from '@shared/quick-ask'

/** More than this many uncollected submissions means nobody is collecting; drop the oldest. */
const MAX_WAITING = 20

/**
 * Submissions waiting for the main window. Main never pushes the text
 * itself: it pings the window, and the window's renderer collects with
 * `take()` — on the ping, and again when it mounts. A window that is still
 * being created or reloading therefore cannot miss one, and nothing is ever
 * delivered twice.
 */
export class QuickAskInbox {
  private waiting: QuickAskSubmission[] = []

  push(submission: QuickAskSubmission): void {
    this.waiting.push(submission)
    if (this.waiting.length > MAX_WAITING) this.waiting.splice(0, this.waiting.length - MAX_WAITING)
  }

  /** Everything waiting, oldest first; the inbox is empty afterwards. */
  take(): QuickAskSubmission[] {
    return this.waiting.splice(0)
  }

  get size(): number {
    return this.waiting.length
  }
}
