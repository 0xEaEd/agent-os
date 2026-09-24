import { PenLine, ShieldCheck } from 'lucide-react'
import { t } from '~/i18n'
import { Sheet } from '../parts'
import { MISSION_PRESETS, PRESET_GROUPS, type MissionPreset, type PresetGroup } from './presets'

/**
 * The catalogue. A mission spends real money, so the watch-only group is
 * first and says so on every card: there is a way to try the machine before
 * any of the user's money rides on it. Choosing a card opens the contract
 * with its two or three numbers already in place.
 */
export function MissionPicker({
  onPick,
  onCustom,
  onClose,
}: {
  onPick: (preset: MissionPreset) => void
  onCustom: () => void
  onClose: () => void
}) {
  return (
    <Sheet title={t('trading.preset.pick.title')} onClose={onClose} widest>
      <div className="trd-picker" data-testid="mission-picker">
        <p className="trd-picker__lead">{t('trading.preset.pick.lead')}</p>
        {PRESET_GROUPS.map((group) => (
          <Group key={group} group={group} onPick={onPick} />
        ))}
        <button
          type="button"
          className="trd-picker__card trd-picker__card--custom app-no-drag"
          onClick={onCustom}
          data-testid="preset-custom"
        >
          <PenLine className="size-4" strokeWidth={1.75} aria-hidden />
          <span className="trd-picker__text">
            <b>{t('trading.preset.custom')}</b>
            <span>{t('trading.preset.custom.hint')}</span>
          </span>
        </button>
      </div>
    </Sheet>
  )
}

function Group({ group, onPick }: { group: PresetGroup; onPick: (preset: MissionPreset) => void }) {
  const presets = MISSION_PRESETS.filter((p) => p.group === group)
  if (presets.length === 0) return null
  return (
    <section className="trd-picker__group" data-group={group}>
      <h3>{t(`trading.preset.group.${group}`)}</h3>
      {group === 'watch' ? <p>{t('trading.preset.group.watch.lead')}</p> : null}
      <div className="trd-picker__grid">
        {presets.map((preset) => {
          const Icon = preset.icon
          return (
            <button
              key={preset.id}
              type="button"
              className="trd-picker__card app-no-drag"
              onClick={() => onPick(preset)}
              data-testid={`preset-${preset.id}`}
            >
              <Icon className="size-4" strokeWidth={1.75} aria-hidden />
              <span className="trd-picker__text">
                {/* The badge rides the title line; put it beside the whole
                    text block and it squeezes the card into one word a line. */}
                <span className="trd-picker__title">
                  <b>{t(preset.name)}</b>
                  {preset.readOnly ? (
                    <span className="trd-picker__safe">
                      <ShieldCheck className="size-3" strokeWidth={2} aria-hidden />
                      {t('trading.preset.readOnly')}
                    </span>
                  ) : null}
                </span>
                <span>{t(preset.hint)}</span>
              </span>
            </button>
          )
        })}
      </div>
    </section>
  )
}
