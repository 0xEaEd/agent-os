import { OctagonX, Pause, Pencil, Play, Trash2, Zap } from 'lucide-react'
import { useEffect, useState, useSyncExternalStore } from 'react'
import type { RawJob } from '@/views/cron/logic'
import { Button } from '~/components/ui/button'
import { t } from '~/i18n'
import type { Mandate } from '../types'
import { missionStatus, type MissionState } from './desk-logic'
import {
  mandateBuysText,
  mandateMissesText,
  mandateProgress,
  mandateProgressText,
  mandateReason,
  mandateRows,
  mandateState,
} from './mandate-logic'

const NO_MANDATES: Mandate[] = []
/** A second click on Stop within this long stops the mandate; after it, the button disarms. */
const STOP_ARM_MS = 4000

function clock(ts: number | null): string {
  if (!ts) return ''
  return new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit' }).format(ts)
}

export function missionWord(state: MissionState, until: number | null): string {
  switch (state) {
    case 'running':
      return t('trading.mission.state.running')
    case 'awaiting':
      return t('trading.mission.state.awaiting')
    case 'sleeping':
      return until
        ? `${t('trading.mission.state.sleeping')} · ${t('trading.mission.until')} ${clock(until)}`
        : t('trading.mission.state.sleeping')
    case 'paused':
      return t('trading.mission.state.paused')
    case 'failed':
      return t('trading.mission.state.failed')
    default:
      return t('trading.mission.state.done')
  }
}

/**
 * A mandate's state word: "Awaiting approval", "Active · next 3 h 12 m",
 * "Active · buy due", "Paused", "Done". The engine's figures, in words.
 */
export function mandateWord(m: Mandate, now: number): string {
  const s = mandateState(m, now)
  if (s.next) return `${t(s.key)} · ${t('trading.dca.next')} ${s.next}`
  if (s.due) return `${t(s.key)} · ${t('trading.dca.due')}`
  return t(s.key)
}

/*
 * One second-tick shared by every DCA countdown on screen — the Missions
 * rows, the strip above the composer, the status strip's chip — so two
 * surfaces never read two clocks and disagree about the same buy (one said
 * "next 59 m" while the other, on a staler clock, still said "next 1 h").
 */
const clockListeners = new Set<() => void>()
let clockNow = 0
let clockTimer: ReturnType<typeof setInterval> | null = null

function subscribeClock(listener: () => void): () => void {
  clockListeners.add(listener)
  if (clockTimer === null) {
    clockNow = Date.now()
    clockTimer = setInterval(() => {
      clockNow = Date.now()
      for (const l of clockListeners) l()
    }, 1_000)
  }
  return () => {
    clockListeners.delete(listener)
    if (clockListeners.size === 0 && clockTimer !== null) {
      clearInterval(clockTimer)
      clockTimer = null
    }
  }
}

// While nothing ticks, the second the render happens in: stable across the
// reads of one render, never a stale tick from an earlier mount.
const readClock = () => (clockTimer === null ? Math.floor(Date.now() / 1_000) * 1_000 : clockNow)
const noClock = () => () => {}

/** The shared clock, ticking while any listed mandate has a next buy to count down to. */
export function useMandateClock(mandates: readonly Mandate[]): number {
  const ticking = mandates.some((m) => m.status === 'active' && Boolean(m.schedule.nextRunAt))
  return useSyncExternalStore(ticking ? subscribeClock : noClock, readClock)
}

/** "Paused by you" for the operator's own bare pause or stop; the engine's words otherwise. */
function reasonTitle(m: Mandate): string | null {
  const reason = mandateReason(m)
  if (reason !== 'user') return reason
  return m.status === 'paused' ? t('trading.dca.reason.pausedByYou') : t('trading.dca.reason.byYou')
}

/** "$120 / $300" over a hairline bar: the engine's spent against its cap. */
function MandateProgress({ mandate }: { mandate: Mandate }) {
  const pct = Math.round(mandateProgress(mandate) * 100)
  return (
    <span
      className="trd-mprog"
      role="meter"
      aria-label={t('trading.dca.progress')}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={pct}
      data-testid="mandate-progress"
    >
      <span className="trd-mprog__bar" aria-hidden>
        <span className="trd-mprog__fill" style={{ width: `${pct}%` }} />
      </span>
      <span className="trd-mprog__text trd-mono">{mandateProgressText(mandate)}</span>
    </span>
  )
}

/**
 * The one-line band above the composer that says what the missions are
 * doing. Renders nothing when there is nothing running: a band that says
 * IDLE over an idle composer is chrome, not information.
 */
export function MissionStrip({
  missions,
  running,
  pendingApprovals,
  mandates = NO_MANDATES,
}: {
  missions: RawJob[]
  running: ReadonlySet<string>
  pendingApprovals: number
  /** The desk's DCA mandates, listed after the cron missions. */
  mandates?: Mandate[]
}) {
  const now = useMandateClock(mandates)
  const { rows, more } = mandateRows(mandates)
  if (missions.length === 0 && mandates.length === 0) return null
  return (
    <div className="trd-mstrip" role="status" data-testid="mission-strip">
      {missions.map((job) => {
        const s = missionStatus(job, {
          running: Boolean(job.id && running.has(job.id)),
          pendingApprovals,
        })
        return (
          <span key={job.id ?? job.name} className="trd-mstrip__item" data-state={s.state}>
            <span className="trd-mstrip__dot" aria-hidden />
            <b>{job.name}</b>
            <span className="trd-mstrip__word">{missionWord(s.state, s.until)}</span>
          </span>
        )
      })}
      {rows.map((m) => (
        <span
          key={m.id}
          className="trd-mstrip__item"
          data-kind="mandate"
          data-state={m.status}
          data-testid="mission-strip-mandate"
        >
          <span className="trd-mstrip__dot" aria-hidden />
          <b>{m.name}</b>
          <span className="trd-mstrip__word">{mandateWord(m, now)}</span>
          <span className="trd-mstrip__word trd-mono">{mandateProgressText(m)}</span>
        </span>
      ))}
      {more > 0 ? (
        <span className="trd-mstrip__more" data-testid="mission-strip-more">
          {t('trading.dca.more').replace('{count}', String(more))}
        </span>
      ) : null}
    </div>
  )
}

/**
 * Status-gated controls for the missions this chat runs. Every refusal the
 * scheduler returns is shown as copy by the hook; nothing here fails silently.
 */
export function MissionControls({
  missions,
  running,
  pendingApprovals,
  busy,
  onStart,
  onEdit,
  onRun,
  onSetEnabled,
  onRemove,
  showStart = true,
  mandates = NO_MANDATES,
  mandateBusy = null,
  onMandatePause,
  onMandateResume,
  onMandateRun,
  onMandateEdit,
  onMandateStop,
}: {
  missions: RawJob[]
  running: ReadonlySet<string>
  pendingApprovals: number
  busy: boolean
  onStart: () => void
  onEdit: (job: RawJob) => void
  onRun: (job: RawJob) => void
  onSetEnabled: (job: RawJob, enabled: boolean) => void
  onRemove: (job: RawJob) => void
  /** The seats row carries its own Start chip when the controls sit above it. */
  showStart?: boolean
  /** DCA mandates, listed after the cron missions with their own controls. */
  mandates?: Mandate[]
  /** The mandate with a write in flight: its row is locked until it lands. */
  mandateBusy?: string | null
  onMandatePause?: (m: Mandate) => void
  onMandateResume?: (m: Mandate) => void
  onMandateRun?: (m: Mandate) => void
  onMandateEdit?: (m: Mandate) => void
  onMandateStop?: (m: Mandate) => void
}) {
  const now = useMandateClock(mandates)
  const { rows, more } = mandateRows(mandates)
  return (
    <div className="trd-mctl" data-testid="mission-controls">
      {missions.map((job) => {
        const live = Boolean(job.id && running.has(job.id))
        const s = missionStatus(job, { running: live, pendingApprovals })
        const enabled = job.enabled !== false
        return (
          <div key={job.id ?? job.name} className="trd-mctl__row" data-state={s.state}>
            <span className="trd-mctl__name" title={job.name}>
              {job.name}
            </span>
            {enabled ? (
              <Button
                variant="ghost"
                size="icon"
                disabled={busy || live}
                aria-label={t('trading.mission.stop')}
                title={t('trading.mission.stop')}
                onClick={() => onSetEnabled(job, false)}
                data-testid="mission-stop"
              >
                <Pause className="size-3.5" strokeWidth={1.75} aria-hidden />
              </Button>
            ) : (
              <Button
                variant="ghost"
                size="icon"
                disabled={busy}
                aria-label={t('trading.mission.continue')}
                title={t('trading.mission.continue')}
                onClick={() => onSetEnabled(job, true)}
                data-testid="mission-continue"
              >
                <Play className="size-3.5" strokeWidth={1.75} aria-hidden />
              </Button>
            )}
            <Button
              variant="ghost"
              size="icon"
              disabled={busy || live || s.state === 'awaiting'}
              aria-label={t('trading.mission.runNow')}
              title={t('trading.mission.runNow')}
              onClick={() => onRun(job)}
              data-testid="mission-run"
            >
              <Zap className="size-3.5" strokeWidth={1.75} aria-hidden />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              disabled={busy || s.state === 'awaiting'}
              aria-label={t('trading.mission.edit')}
              title={t('trading.mission.edit')}
              onClick={() => onEdit(job)}
              data-testid="mission-edit"
            >
              <Pencil className="size-3.5" strokeWidth={1.75} aria-hidden />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              disabled={busy || live}
              aria-label={t('trading.mission.remove')}
              title={t('trading.mission.remove')}
              onClick={() => onRemove(job)}
              data-testid="mission-remove"
            >
              <Trash2 className="size-3.5" strokeWidth={1.75} aria-hidden />
            </Button>
          </div>
        )
      })}
      {rows.map((m) => (
        <MandateRow
          key={m.id}
          mandate={m}
          now={now}
          busy={mandateBusy === m.id}
          onPause={onMandatePause}
          onResume={onMandateResume}
          onRun={onMandateRun}
          onEdit={onMandateEdit}
          onStop={onMandateStop}
        />
      ))}
      {more > 0 ? (
        <span className="trd-mctl__more" data-testid="mandate-more">
          {t('trading.dca.more').replace('{count}', String(more))}
        </span>
      ) : null}
      {showStart ? (
        <button
          type="button"
          className="trd-mctl__start app-no-drag"
          onClick={onStart}
          data-testid="mission-start"
        >
          <Zap className="size-3" strokeWidth={2.25} aria-hidden />
          {t('trading.mission.start')}
        </button>
      ) : null}
    </div>
  )
}

/**
 * One DCA mandate among the missions: its name, what it is doing, spent of
 * cap, and the controls its state allows. A pending one is decided on its
 * card (the approvals region), so its row only points there; a finished one
 * has nothing left to steer. Stop is final, so it asks for a second click —
 * inline, never a dialog.
 */
function MandateRow({
  mandate: m,
  now,
  busy,
  onPause,
  onResume,
  onRun,
  onEdit,
  onStop,
}: {
  mandate: Mandate
  now: number
  busy: boolean
  onPause?: (m: Mandate) => void
  onResume?: (m: Mandate) => void
  onRun?: (m: Mandate) => void
  onEdit?: (m: Mandate) => void
  onStop?: (m: Mandate) => void
}) {
  const [armed, setArmed] = useState(false)
  useEffect(() => {
    if (!armed) return
    const id = window.setTimeout(() => setArmed(false), STOP_ARM_MS)
    return () => window.clearTimeout(id)
  }, [armed])
  const steerable = m.status === 'active' || m.status === 'paused'
  // Why it stopped or paused ("cap reached", "paused after 3 runs: …"), on hover.
  const reason = reasonTitle(m)
  const misses = mandateMissesText(m)
  return (
    <div
      className="trd-mctl__row"
      data-kind="mandate"
      data-state={m.status}
      data-testid="mandate-row"
      data-mandate={m.id}
      title={reason ?? undefined}
    >
      <span className="trd-mctl__kind" aria-hidden>
        {t('trading.dca.kind')}
      </span>
      <span className="trd-mctl__name" title={reason ? `${m.name} · ${reason}` : m.name}>
        {m.name}
      </span>
      <span className="trd-mctl__word" data-testid="mandate-word">
        {mandateWord(m, now)}
      </span>
      <span className="trd-mctl__runs trd-mono" data-testid="mandate-runs">
        {mandateBuysText(m)}
        {misses ? <span className="trd-mctl__misses"> {misses}</span> : null}
      </span>
      <MandateProgress mandate={m} />
      {m.status === 'awaiting_approval' ? (
        <span className="trd-mctl__hint">{t('trading.dca.review')}</span>
      ) : null}
      {steerable ? (
        <>
          {m.status === 'active' ? (
            <Button
              variant="ghost"
              size="icon"
              disabled={busy}
              aria-label={t('trading.dca.pause')}
              title={t('trading.dca.pause')}
              onClick={() => onPause?.(m)}
              data-testid="mandate-pause"
            >
              <Pause className="size-3.5" strokeWidth={1.75} aria-hidden />
            </Button>
          ) : (
            <Button
              variant="ghost"
              size="icon"
              disabled={busy}
              aria-label={t('trading.dca.resume')}
              title={t('trading.dca.resume')}
              onClick={() => onResume?.(m)}
              data-testid="mandate-resume"
            >
              <Play className="size-3.5" strokeWidth={1.75} aria-hidden />
            </Button>
          )}
          <Button
            variant="ghost"
            size="icon"
            disabled={busy}
            aria-label={t('trading.dca.runNow')}
            title={t('trading.dca.runNow')}
            onClick={() => onRun?.(m)}
            data-testid="mandate-run"
          >
            <Zap className="size-3.5" strokeWidth={1.75} aria-hidden />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            disabled={busy}
            aria-label={t('trading.dca.edit')}
            title={t('trading.dca.edit')}
            onClick={() => onEdit?.(m)}
            data-testid="mandate-edit"
          >
            <Pencil className="size-3.5" strokeWidth={1.75} aria-hidden />
          </Button>
          {armed ? (
            <button
              type="button"
              className="trd-mctl__confirm app-no-drag"
              disabled={busy}
              onClick={() => {
                setArmed(false)
                onStop?.(m)
              }}
              data-testid="mandate-stop"
              data-armed
            >
              {t('trading.dca.stopAgain')}
            </button>
          ) : (
            <Button
              variant="ghost"
              size="icon"
              disabled={busy}
              aria-label={t('trading.dca.stop')}
              title={t('trading.dca.stop')}
              onClick={() => setArmed(true)}
              data-testid="mandate-stop"
            >
              <OctagonX className="size-3.5" strokeWidth={1.75} aria-hidden />
            </Button>
          )}
        </>
      ) : null}
    </div>
  )
}
