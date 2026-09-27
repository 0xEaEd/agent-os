import { Pause, Pencil, Play, Trash2, Zap } from 'lucide-react'
import type { RawJob } from '@/views/cron/logic'
import { Button } from '~/components/ui/button'
import { t } from '~/i18n'
import { missionStatus, type MissionState } from './desk-logic'

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
 * The one-line band above the composer that says what the missions are
 * doing. Renders nothing when there is nothing running: a band that says
 * IDLE over an idle composer is chrome, not information.
 */
export function MissionStrip({
  missions,
  running,
  pendingApprovals,
}: {
  missions: RawJob[]
  running: ReadonlySet<string>
  pendingApprovals: number
}) {
  if (missions.length === 0) return null
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
}) {
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
