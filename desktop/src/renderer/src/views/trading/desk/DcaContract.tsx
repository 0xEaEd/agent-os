import { ChevronLeft } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Button } from '~/components/ui/button'
import { t } from '~/i18n'
import { sameAddress, shortAddress, walletLabel } from '../logic'
import { Sheet } from '../parts'
import { CHAINS, type Limits, type Mandate, type Wallet } from '../types'
import {
  DCA_EVERY,
  dcaAutoName,
  dcaCapUsd,
  dcaEverySeconds,
  dcaFormFromMandate,
  dcaFormFromPreset,
  dcaUpdatePatch,
  everyPhrase,
  usdShort,
  validateDca,
  type DcaError,
  type DcaForm,
} from './mandate-logic'
import { presetDefaults, type MissionPreset } from './presets'

function positive(text: string): number | null {
  const n = Number(text.replace(/,/g, '').trim())
  return text.trim() && Number.isFinite(n) && n > 0 ? n : null
}

/**
 * The DCA contract. Unlike a cron mission, a DCA is a mandate the engine
 * runs itself (docs/dca.md): the cap, the cadence, the number of buys and
 * the price ceiling are rows in the ledger, not requests in a prompt — so
 * this form sends numbers, not prose, and says plainly that the engine
 * stops the DCA on its own. Editing one sends only what an update may
 * change; the token, the wallet and the chain are fixed once it exists.
 */
export function DcaContract({
  preset,
  mandate,
  wallets,
  primary,
  limits,
  onBack,
  onClose,
  onCreate,
  onUpdate,
}: {
  preset?: MissionPreset | null
  /** Editing an existing mandate. */
  mandate?: Mandate | null
  wallets: readonly Wallet[]
  primary: string | null
  limits: Limits | null
  onBack?: () => void
  onClose: () => void
  /** `trading.dca.create` params (the caller adds the session); null = refused, stay open. */
  onCreate: (form: DcaForm) => Promise<unknown>
  /** Only the changed fields; null = refused, stay open. */
  onUpdate: (mandate: Mandate, patch: Record<string, unknown>) => Promise<unknown>
}) {
  const [form, setForm] = useState<DcaForm>(() =>
    mandate
      ? dcaFormFromMandate(mandate)
      : dcaFormFromPreset(preset ?? null, preset ? presetDefaults(preset) : {}, { primary }),
  )
  // The name follows the token until it is typed by hand.
  const [nameOwned, setNameOwned] = useState(Boolean(mandate))
  const [touched, setTouched] = useState(false)
  const [saving, setSaving] = useState(false)
  const editing = Boolean(mandate)
  const check = validateDca(form)
  const patchOut = useMemo(
    () => (mandate && check.ok ? dcaUpdatePatch(form, mandate) : null),
    [form, mandate, check.ok],
  )
  const unchanged = editing && patchOut !== null && Object.keys(patchOut).length === 0

  const patch = (p: Partial<DcaForm>) => {
    setTouched(true)
    setForm((f) => {
      const next = { ...f, ...p }
      return nameOwned ? next : { ...next, name: dcaAutoName(next) }
    })
  }

  async function submit() {
    setTouched(true)
    if (!check.ok || unchanged) return
    setSaving(true)
    try {
      const res = mandate && patchOut ? await onUpdate(mandate, patchOut) : await onCreate(form)
      // A refusal has already been toasted; closing would throw the form away.
      if (res === null || res === false) return
      onClose()
    } finally {
      setSaving(false)
    }
  }

  const every = dcaEverySeconds(form)
  const cap = dcaCapUsd(form)
  const usd = positive(form.usd)
  const tokenText = form.token.trim() || 'ETH'
  const summary =
    usd !== null && every !== null && cap !== null
      ? [
          t('trading.dca.contract.summary')
            .replace('{usd}', usdShort(usd))
            .replace('{token}', tokenText)
            .replace('{every}', everyPhrase(every))
            .replace('{cap}', usdShort(cap)),
          positive(form.runs) !== null
            ? t('trading.dca.contract.summaryRuns').replace(
                '{runs}',
                String(Math.floor(Number(form.runs))),
              )
            : '',
          positive(form.maxPrice) !== null
            ? t('trading.dca.contract.summaryMax')
                .replace('{token}', tokenText)
                .replace('{max}', usdShort(positive(form.maxPrice)))
            : '',
        ]
          .filter(Boolean)
          .join(' ')
      : ''
  const overThreshold =
    limits && usd !== null && limits.thresholdUsd > 0 && usd > limits.thresholdUsd
      ? t('trading.dca.contract.overThreshold')
          .replace('{usd}', usdShort(usd))
          .replace('{threshold}', usdShort(limits.thresholdUsd))
      : ''
  const robinhood = form.chainId === 4663

  const title = editing
    ? t('trading.dca.contract.editTitle')
    : preset
      ? t(preset.name)
      : t('trading.preset.dca.name')

  return (
    <Sheet
      title={title}
      onClose={onClose}
      wide
      foot={
        <>
          <Button onClick={onClose}>{t('trading.contract.cancel')}</Button>
          <Button
            variant="primary"
            disabled={!check.ok || saving || unchanged}
            onClick={() => void submit()}
            data-testid="dca-submit"
          >
            {editing ? t('trading.dca.contract.save') : t('trading.dca.contract.start')}
          </Button>
        </>
      }
      note={
        touched && !check.ok && check.error ? (
          <span className="text-danger" data-testid="dca-error">
            {t(`trading.dca.error.${check.error as DcaError}`)}
          </span>
        ) : unchanged && touched ? (
          <span className="text-muted-foreground">{t('trading.dca.contract.unchanged')}</span>
        ) : null
      }
    >
      <div className="trd-contract trd-dca" data-testid="dca-contract">
        {onBack || preset ? (
          <div className="trd-contract__preset">
            {onBack ? (
              <button
                type="button"
                className="trd-contract__back app-no-drag"
                onClick={onBack}
                data-testid="contract-back"
              >
                <ChevronLeft className="size-3.5" strokeWidth={2} aria-hidden />
                {t('trading.preset.back')}
              </button>
            ) : null}
            {preset ? <p>{t(preset.hint)}</p> : null}
          </div>
        ) : null}

        <div className="trd-contract__knobs">
          <label className="trd-field">
            <span>{t('trading.dca.contract.token')}</span>
            <input
              className="mac-input trd-mono"
              value={form.token}
              disabled={editing}
              spellCheck={false}
              autoCapitalize="characters"
              onChange={(e) => {
                const v = e.target.value
                patch({ token: /^0x/i.test(v) ? v : v.toUpperCase() })
              }}
              data-testid="dca-token"
            />
          </label>
          <label className="trd-field">
            <span>{t('trading.dca.contract.usd')}</span>
            <input
              className="mac-input trd-mono"
              inputMode="decimal"
              value={form.usd}
              onChange={(e) => patch({ usd: e.target.value })}
              data-testid="dca-usd"
            />
          </label>
          <label className="trd-field">
            <span>{t('trading.dca.contract.quote')}</span>
            <input
              className="mac-input trd-mono"
              value={form.quote}
              disabled={editing}
              spellCheck={false}
              placeholder={
                robinhood
                  ? t('trading.dca.contract.quoteRobinhood')
                  : t('trading.dca.contract.quoteDefault')
              }
              onChange={(e) => {
                const v = e.target.value
                patch({ quote: /^0x/i.test(v) ? v : v.toUpperCase() })
              }}
              data-testid="dca-quote"
            />
          </label>
        </div>

        <fieldset className="trd-field trd-dca__every">
          <legend>{t('trading.dca.contract.every')}</legend>
          <div className="trd-dca__chips" role="group">
            {DCA_EVERY.map((c) => (
              <button
                key={c.key}
                type="button"
                className="trd-dca__chip app-no-drag"
                aria-pressed={form.everyCustom === null && form.everySeconds === c.seconds}
                onClick={() => patch({ everySeconds: c.seconds, everyCustom: null })}
                data-testid={`dca-every-${c.key}`}
              >
                {c.key}
              </button>
            ))}
            <button
              type="button"
              className="trd-dca__chip app-no-drag"
              aria-pressed={form.everyCustom !== null}
              onClick={() => patch({ everyCustom: form.everyCustom ?? '' })}
              data-testid="dca-every-custom"
            >
              {t('trading.dca.contract.custom')}
            </button>
            {form.everyCustom !== null ? (
              <input
                className="mac-input trd-mono trd-dca__custom"
                value={form.everyCustom}
                placeholder={t('trading.dca.contract.customHint')}
                aria-label={t('trading.dca.contract.custom')}
                spellCheck={false}
                autoFocus={!editing}
                onChange={(e) => patch({ everyCustom: e.target.value })}
                data-testid="dca-every-input"
              />
            ) : null}
          </div>
        </fieldset>

        <div className="trd-contract__knobs">
          <label className="trd-field">
            <span>{t('trading.dca.contract.cap')}</span>
            <input
              className="mac-input trd-mono"
              inputMode="decimal"
              value={form.cap}
              onChange={(e) => patch({ cap: e.target.value })}
              data-testid="dca-cap"
            />
          </label>
          <label className="trd-field">
            <span>
              {t('trading.dca.contract.runs')}{' '}
              <em className="trd-dca__opt">{t('trading.dca.contract.optional')}</em>
            </span>
            <input
              className="mac-input trd-mono"
              inputMode="numeric"
              value={form.runs}
              onChange={(e) => patch({ runs: e.target.value })}
              data-testid="dca-runs"
            />
          </label>
          <label className="trd-field">
            <span>
              {t('trading.dca.contract.maxPrice')}{' '}
              <em className="trd-dca__opt">{t('trading.dca.contract.optional')}</em>
            </span>
            <input
              className="mac-input trd-mono"
              inputMode="decimal"
              value={form.maxPrice}
              onChange={(e) => patch({ maxPrice: e.target.value })}
              data-testid="dca-max-price"
            />
          </label>
        </div>

        {summary ? (
          <p className="trd-dca__summary trd-num" data-testid="dca-summary">
            {summary}
          </p>
        ) : null}
        {/* The opposite of the cron form's "goals, not limits": here the
            engine counts every dollar and ends the DCA itself. */}
        <p className="trd-contract__limits trd-dca__enforced" data-testid="dca-enforced">
          {t('trading.dca.contract.enforced').replace('{cap}', cap !== null ? usdShort(cap) : '—')}
        </p>
        {overThreshold ? (
          <p className="trd-contract__caveat" data-testid="dca-over-threshold">
            {overThreshold}
          </p>
        ) : null}

        <div className="trd-contract__grid">
          <label className="trd-field">
            <span>{t('trading.dca.contract.wallet')}</span>
            <select
              className="mac-input"
              value={form.wallet}
              disabled={editing}
              onChange={(e) => patch({ wallet: e.target.value })}
              data-testid="dca-wallet"
            >
              {form.wallet && !wallets.some((w) => sameAddress(w.address, form.wallet)) ? (
                // A mandate's wallet the vault list does not carry (yet): shown, not swapped.
                <option value={form.wallet}>
                  {mandate?.wallet.label || shortAddress(form.wallet)}
                </option>
              ) : null}
              {wallets.map((w) => (
                <option key={w.address} value={w.address}>
                  {walletLabel(w)}
                </option>
              ))}
            </select>
          </label>
          <fieldset className="trd-field">
            <legend>{t('trading.dca.contract.chain')}</legend>
            <div className="trd-dca__chips" role="group">
              {CHAINS.map((c) => (
                <button
                  key={c.id}
                  type="button"
                  className="trd-dca__chip app-no-drag"
                  aria-pressed={form.chainId === c.id}
                  disabled={editing}
                  onClick={() => patch({ chainId: c.id })}
                  data-testid={`dca-chain-${c.key}`}
                >
                  {c.short}
                </button>
              ))}
            </div>
          </fieldset>
        </div>

        <div className="trd-contract__grid">
          <label className="trd-field">
            <span>{t('trading.dca.contract.name')}</span>
            <input
              className="mac-input"
              value={form.name}
              onChange={(e) => {
                setNameOwned(true)
                setTouched(true)
                setForm((f) => ({ ...f, name: e.target.value }))
              }}
              data-testid="dca-name"
            />
          </label>
          {editing ? null : (
            <label className="trd-contract__check trd-dca__start">
              <input
                type="checkbox"
                checked={form.startNow}
                onChange={(e) => patch({ startNow: e.target.checked })}
                data-testid="dca-start-now"
              />
              <span>{t('trading.dca.contract.startNow')}</span>
            </label>
          )}
        </div>
        {editing ? (
          <p className="trd-contract__limits">{t('trading.dca.contract.locked')}</p>
        ) : null}
      </div>
    </Sheet>
  )
}
