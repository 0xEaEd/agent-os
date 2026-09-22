// Router section (setup.js:550-635,1790-1855). Mode (Pilot / LLM judge / Jev /
// Off), default text model tier, judge model, pilot safety-net threshold, the
// Jev key + high-risk floor, and the editable tier table. Save via onboarding.router.configure, gated on the
// provider being saved (effective === configured).
import { useCallback, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { toast } from 'sonner'
import { useRpc } from '@/app/providers'
import { Button } from '@/components/ui/button'
import { t } from '@/i18n'
import '@/i18n/en/setup'
import { PanelHead, SetupCheckbox, SetupSelect } from './parts'
import {
  buildRouterConfigureParams,
  classifyRouterModels,
  configuredProvider as configuredProviderFn,
  effectiveProvider as effectiveProviderFn,
  isVisibleTier,
  mergeModelOptions,
  mergeTiers,
  modelOptionLabel,
  modelOptionMeta,
  offlineTierModels,
  resolveJudgeModelParam,
  routerMode as routerModeFn,
  tierLabel,
  TEXT_TIERS,
  type Catalog,
  type ModelListEntry,
  type OnboardingStatus,
  type RouterConfigureParams,
  type RouterMode,
  type SetupConfig,
  type TierSpec,
} from './logic'

const THINKING_LEVELS = ['', 'off', 'none', 'minimal', 'low', 'medium', 'high', 'xhigh']

interface TierRowState {
  provider: string
  model: string
  thinkingLevel: string
  supportsImage: boolean
}

export function RouterSection({
  catalog,
  status,
  config,
  draftProvider = '',
  onSave,
  onBack,
  onNext,
  saving,
}: {
  catalog: Catalog
  status: OnboardingStatus
  config: SetupConfig
  // The provider drafted in the Provider step (not yet saved). Preview/table
  // render on the effective provider — draft OR configured (setup.js:552-556).
  draftProvider?: string
  onSave: (params: RouterConfigureParams) => void
  onBack: () => void
  onNext: () => void
  saving: boolean
}) {
  const router = config.agentos_router || {}
  const rpc = useRpc()
  const provider = effectiveProviderFn(status, config, draftProvider)
  const configured = configuredProviderFn(status, config)
  const canSave = Boolean(provider && provider === configured)

  const routerCatalog = catalog.routerProfiles || {}
  const profiles = Array.isArray(routerCatalog.profiles) ? routerCatalog.profiles : []
  const profile = provider ? profiles.find((p) => p?.providerId === provider) : undefined
  const tiers = useMemo(
    () => (provider ? mergeTiers(profile?.tiers, router.tiers) : {}),
    [provider, profile?.tiers, router.tiers],
  )
  const defaultTierInitial = router.default_tier || routerCatalog.defaultTier || 'c1'

  const [mode, setMode] = useState<RouterMode>(routerModeFn(router))
  const [defaultTier, setDefaultTier] = useState(defaultTierInitial)

  const pilotThresholdInitial =
    router.pilot?.safety_net_threshold != null ? String(router.pilot.safety_net_threshold) : '0.5'
  const [pilotThreshold, setPilotThreshold] = useState(pilotThresholdInitial)

  // Jev (typesafe.ai) cloud classifier. The key is write-only: the public
  // config never echoes it back, so the field starts blank and a blank save
  // preserves whatever is persisted (or the TYPESAFE_API_KEY env fallback).
  const [jevApiKey, setJevApiKey] = useState('')
  const jevHighRiskInitial =
    router.jev?.high_risk_threshold != null ? String(router.jev.high_risk_threshold) : '0.7'
  const [jevHighRiskThreshold, setJevHighRiskThreshold] = useState(jevHighRiskInitial)

  // The translation cap is an engine guard rather than a strategy setting, so
  // it stays visible for every mode. An absent key means the default (on).
  const [translateCeilingEnabled, setTranslateCeilingEnabled] = useState(
    router.translate_ceiling_enabled !== false,
  )
  const [translateCeilingTier, setTranslateCeilingTier] = useState(
    router.translate_ceiling_tier || 'c0',
  )

  // Judge model catalog: AUTO is judge_model === null → the empty option.
  const judgeCatalog = routerCatalog.judge || {}
  const judgeProfiles =
    judgeCatalog.profiles &&
    typeof judgeCatalog.profiles === 'object' &&
    !Array.isArray(judgeCatalog.profiles)
      ? judgeCatalog.profiles
      : {}
  const judgeProfile = provider ? judgeProfiles[provider] || {} : {}
  const judgeAutoModel = typeof judgeProfile.autoModel === 'string' ? judgeProfile.autoModel : null
  const judgeModels = Array.isArray(judgeProfile.models)
    ? judgeProfile.models.filter((model): model is string => typeof model === 'string')
    : []
  const judgeLoaded = router.judge_model || ''
  const judgeIsLocal = Boolean(router.judge_base_url)
  const [judge, setJudge] = useState(judgeLoaded)
  const judgeAutoLabel = judgeAutoModel
    ? `Auto (recommended) - ${judgeAutoModel}`
    : 'Auto (recommended)'

  // Editable tier rows (only text tiers + image_model).
  const visibleTiers = Object.entries(tiers).filter(([name]) => isVisibleTier(name))
  const hasImageTier = visibleTiers.some(([name]) => name === 'image_model')

  // The RPC owns both filters (rpc_models.py:58-66). Asking for everything and
  // narrowing here would be indistinguishable from "this provider has no
  // models" whenever the gateway has not loaded that provider's catalog, and
  // it would leave the vision filter guessing at capability data the server
  // already has.
  const modelsQuery = useQuery<ModelListEntry[]>({
    queryKey: ['setup', 'models', provider],
    enabled: Boolean(provider),
    retry: false,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
    queryFn: async () => {
      await rpc.waitForConnection()
      return (await rpc.call<ModelListEntry[]>('models.list', { provider })) ?? []
    },
  })
  const visionQuery = useQuery<ModelListEntry[]>({
    queryKey: ['setup', 'models', provider, 'vision'],
    enabled: Boolean(provider) && hasImageTier,
    retry: false,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
    queryFn: async () => {
      await rpc.waitForConnection()
      return (
        (await rpc.call<ModelListEntry[]>('models.list', {
          provider,
          capabilities: ['vision'],
        })) ?? []
      )
    },
  })

  // Offline options come from the catalog profile, NOT from the merged tiers:
  // a model the operator saved earlier should still be checked against what
  // the catalog actually knows, or a typo goes unflagged forever once saved.
  const textOptions = useMemo(
    () => mergeModelOptions(modelsQuery.data, offlineTierModels(profile?.tiers)),
    [modelsQuery.data, profile?.tiers],
  )
  const visionOptions = useMemo(
    () =>
      mergeModelOptions(visionQuery.data, offlineTierModels(profile?.tiers, { visionOnly: true })),
    [visionQuery.data, profile?.tiers],
  )
  const optionsFor = useCallback(
    (name: string) => (name === 'image_model' ? visionOptions : textOptions),
    [textOptions, visionOptions],
  )
  const [rowKey, setRowKey] = useState(provider)
  const [rows, setRows] = useState<Record<string, TierRowState>>(() => seedRows(visibleTiers))
  if (rowKey !== provider) {
    setRowKey(provider)
    setRows(seedRows(visibleTiers))
  }

  const rowFor = (name: string, tier: TierSpec): TierRowState => rows[name] ?? tierRowState(tier)

  const setRow = (name: string, tier: TierSpec, patch: Partial<TierRowState>) =>
    setRows((current) => ({
      ...current,
      [name]: { ...tierRowState(tier), ...current[name], ...patch },
    }))

  const showJudge = mode === 'llm_judge'
  const showPilot = mode === 'pilot-v1'
  const showJev = mode === 'jev'

  const summary = provider ? `${provider} / ${tierLabel(defaultTier)}` : 'Choose a provider first'

  const collectAndSave = () => {
    if (!canSave) return

    // Warn, never block: unknown ids are legitimate (self-hosted, brand new,
    // offline), silently accepting a typo is not. A tier a request never
    // escalates to can carry a bad model for a long time before the first
    // failed turn.
    const warnings = classifyRouterModels(
      visibleTiers.map(([name, tier]) => ({ tier: name, model: rowFor(name, tier).model })),
      textOptions,
      visionOptions,
    )
    if (warnings.noCatalog) {
      toast.warning(
        `No model catalog available for ${provider} — tier model ids were not checked.`,
        { id: 'setup-router-no-catalog' },
      )
    }
    if (warnings.unknown.length > 0) {
      const scope = modelsQuery.isPending ? 'the catalog loaded so far' : `${provider}'s catalog`
      toast.warning(
        `Saved, but not in ${scope}: ${warnings.unknown.join(', ')}. Check for a typo.`,
        { id: 'setup-router-unknown-model' },
      )
    }
    if (warnings.nonVision.length > 0) {
      toast.warning(
        `Saved, but the image tier points at a model with no vision capability: ${warnings.nonVision.join(', ')}.`,
        { id: 'setup-router-non-vision-model' },
      )
    }

    const judgeModel = resolveJudgeModelParam(judge, judgeLoaded, judgeIsLocal)
    const params = buildRouterConfigureParams({
      sel: mode,
      defaultTier,
      judgeModel,
      pilotThresholdRaw: pilotThreshold,
      jevApiKeyRaw: jevApiKey,
      jevHighRiskThresholdRaw: jevHighRiskThreshold,
      translateCeilingEnabled,
      translateCeilingTier,
      // Tiers select the MODEL; requests always go through llm.provider, and a
      // tier naming a different provider is degraded back to llm.model at boot
      // (boot.py:1106-1120). The cell is a read-only chip for that reason, so
      // the saved value follows the configured provider rather than whatever a
      // previous free-text edit left behind.
      tiers: visibleTiers.map(([name, tier]) => ({
        tier: name,
        ...rowFor(name, tier),
        provider,
      })),
    })
    onSave(params)
  }

  return (
    <section className="setup-panel panel">
      <PanelHead title={t('setup.routerTitle')} subtitle={summary} />
      <div className="setup-router-toolbar">
        <label>
          <span>{t('setup.routerMode')}</span>
          <SetupSelect
            aria-label={t('setup.routerModeAria')}
            value={mode}
            disabled={!provider}
            onChange={(e) => setMode(e.target.value as RouterMode)}
          >
            <option value="pilot-v1">{t('setup.routerModePilot')}</option>
            <option value="llm_judge">{t('setup.routerModeJudge')}</option>
            <option value="jev">{t('setup.routerModeJev')}</option>
            <option value="disabled">{t('setup.routerModeOff')}</option>
          </SetupSelect>
          {showPilot ? <small className="setup-hint">{t('setup.routerPilotHint')}</small> : null}
          {showJev ? <small className="setup-hint">{t('setup.routerJevHint')}</small> : null}
        </label>
        <label>
          <span>{t('setup.routerDefaultModel')}</span>
          <SetupSelect
            aria-label={t('setup.routerDefaultModel')}
            value={defaultTier}
            disabled={!provider}
            onChange={(e) => setDefaultTier(e.target.value)}
          >
            {TEXT_TIERS.map((t) => (
              <option key={t} value={t}>
                {tierLabel(t)}
              </option>
            ))}
          </SetupSelect>
        </label>
        {showJudge ? (
          <label>
            <span>{t('setup.routerJudgeModel')}</span>
            <SetupSelect
              aria-label={t('setup.routerJudgeModel')}
              value={judge}
              onChange={(e) => setJudge(e.target.value)}
            >
              <option value="">{judgeAutoLabel}</option>
              {judgeModels.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </SetupSelect>
          </label>
        ) : null}
        {showPilot ? (
          <label>
            <span>{t('setup.routerPilotSafetyNet')}</span>
            <input
              type="number"
              min={0}
              max={1}
              step={0.05}
              aria-label={t('setup.routerPilotThresholdAria')}
              value={pilotThreshold}
              onChange={(e) => setPilotThreshold(e.target.value)}
            />
            <small className="setup-hint">{t('setup.routerPilotThresholdHint')}</small>
          </label>
        ) : null}
        {showJev ? (
          <>
            <label>
              <span>{t('setup.routerJevApiKey')}</span>
              <input
                type="password"
                autoComplete="off"
                aria-label={t('setup.routerJevApiKeyAria')}
                placeholder={t('setup.routerJevApiKeyPlaceholder')}
                value={jevApiKey}
                onChange={(e) => setJevApiKey(e.target.value)}
              />
            </label>
            <label>
              <span>{t('setup.routerJevHighRisk')}</span>
              <input
                type="number"
                min={0}
                max={1}
                step={0.05}
                aria-label={t('setup.routerJevHighRiskAria')}
                value={jevHighRiskThreshold}
                onChange={(e) => setJevHighRiskThreshold(e.target.value)}
              />
              <small className="setup-hint">{t('setup.routerJevHighRiskHint')}</small>
            </label>
          </>
        ) : null}
        <label>
          <span>{t('setup.routerTranslateCap')}</span>
          <SetupSelect
            aria-label={t('setup.routerTranslateCapAria')}
            value={translateCeilingEnabled ? translateCeilingTier : 'off'}
            disabled={!provider}
            onChange={(e) => {
              const next = e.target.value
              if (next === 'off') {
                setTranslateCeilingEnabled(false)
                return
              }
              setTranslateCeilingEnabled(true)
              setTranslateCeilingTier(next)
            }}
          >
            <option value="off">{t('setup.routerTranslateOff')}</option>
            {TEXT_TIERS.map((t) => (
              <option key={t} value={t}>
                {tierLabel(t)}
              </option>
            ))}
          </SetupSelect>
          <small className="setup-hint">{t('setup.routerTranslateHint')}</small>
        </label>
      </div>

      {provider ? (
        <div className="setup-tier-table" role="table">
          <div className="setup-tier-table__row is-head" role="row">
            <span role="columnheader">{t('setup.routerColTier')}</span>
            <span role="columnheader">{t('setup.routerColProvider')}</span>
            <span role="columnheader">{t('setup.routerColModel')}</span>
            <span role="columnheader">{t('setup.routerColThinking')}</span>
            <span role="columnheader">{t('setup.routerColImage')}</span>
          </div>
          {visibleTiers.map(([name, tier]) => {
            // A coherent settings snapshot can add a tier while this mounted
            // editor keeps another tier draft. Seed newly visible rows from
            // their catalog/config spec instead of dereferencing stale state.
            const row = rowFor(name, tier)
            const isImageModel = name === 'image_model'
            const supportsImage = isImageModel || row.supportsImage
            const options = optionsFor(name)
            const listId = `setup-tier-models-${name}`
            // Browsers disagree about whether a <datalist> option's label is
            // shown at all (Safari renders values only), so the numbers that
            // decide a tier choice are also rendered under the input for
            // whatever is currently entered.
            const selectedMeta = modelOptionMeta(
              options.find((option) => option.id === row.model) ?? { id: '', name: '' },
            )
            return (
              <div className="setup-tier-table__row" role="row" key={name}>
                <div className="setup-tier-table__cell setup-tier-table__cell--tier" role="cell">
                  <span className="setup-tier-table__mobile-label" aria-hidden="true">
                    {t('setup.routerColTier')}
                  </span>
                  <code>{name}</code>
                </div>
                <div className="setup-tier-table__cell" role="cell">
                  <span className="setup-tier-table__mobile-label" aria-hidden="true">
                    {t('setup.routerColProvider')}
                  </span>
                  {/* Read-only: five editable copies of one value are five
                      chances to get it wrong, and the runtime ignores the
                      difference anyway. */}
                  <code
                    className="setup-provider-chip"
                    aria-label={t('setup.routerRowProvider', { tier: name })}
                  >
                    {provider}
                  </code>
                </div>
                <div className="setup-tier-table__cell setup-tier-table__cell--model" role="cell">
                  <span className="setup-tier-table__mobile-label" aria-hidden="true">
                    {t('setup.routerColModel')}
                  </span>
                  <input
                    aria-label={t('setup.routerRowModel', { tier: name })}
                    value={row.model}
                    list={options.length > 0 ? listId : undefined}
                    autoComplete="off"
                    // The column is narrower than a real model id.
                    title={row.model}
                    onChange={(e) => setRow(name, tier, { model: e.target.value })}
                  />
                  {options.length > 0 ? (
                    <datalist id={listId}>
                      {options.map((option) => (
                        <option key={option.id} value={option.id}>
                          {modelOptionLabel(option)}
                        </option>
                      ))}
                    </datalist>
                  ) : null}
                  {options.length === 0 && !modelsQuery.isPending ? (
                    <small className="setup-hint setup-hint--field">
                      {isImageModel
                        ? `No vision-capable models known for ${provider} — type an id.`
                        : `No catalog models known for ${provider} — type an id.`}
                    </small>
                  ) : null}
                  {selectedMeta ? (
                    <small className="setup-hint setup-hint--field">{selectedMeta}</small>
                  ) : null}
                </div>
                <div className="setup-tier-table__cell" role="cell">
                  <span className="setup-tier-table__mobile-label" aria-hidden="true">
                    {t('setup.routerColThinking')}
                  </span>
                  <SetupSelect
                    aria-label={t('setup.routerRowThinking', { tier: name })}
                    value={row.thinkingLevel}
                    onChange={(e) => setRow(name, tier, { thinkingLevel: e.target.value })}
                  >
                    {THINKING_LEVELS.map((v) => (
                      <option key={v} value={v}>
                        {v || '-'}
                      </option>
                    ))}
                  </SetupSelect>
                </div>
                <div className="setup-tier-table__cell setup-tier-table__cell--image" role="cell">
                  <span className="setup-tier-table__mobile-label" aria-hidden="true">
                    {t('setup.routerColImage')}
                  </span>
                  <SetupCheckbox
                    ariaLabel={t('setup.routerRowImage', { tier: name })}
                    checked={supportsImage}
                    className="setup-check--compact"
                    disabled={isImageModel}
                    onChange={(checked) => setRow(name, tier, { supportsImage: checked })}
                  >
                    {supportsImage ? t('setup.routerImageOn') : t('setup.routerImageOff')}
                  </SetupCheckbox>
                </div>
              </div>
            )
          })}
        </div>
      ) : (
        <div className="setup-warning panel tone-warn tone-rail">
          {t('setup.routerNeedsProviderWarning')}
        </div>
      )}

      {provider && !canSave ? (
        <div className="setup-warning panel tone-warn tone-rail">
          {t('setup.routerSaveProviderFirst')}
        </div>
      ) : null}

      <div className="setup-actions">
        <Button type="button" variant="outline" onClick={onBack}>
          {t('setup.back')}
        </Button>
        <Button type="button" disabled={!canSave || saving} onClick={collectAndSave}>
          {t('setup.routerSave')}
        </Button>
        <Button type="button" variant="outline" onClick={onNext}>
          {t('setup.next')}
        </Button>
      </div>
    </section>
  )
}

function seedRows(entries: Array<[string, TierSpec]>): Record<string, TierRowState> {
  const rows: Record<string, TierRowState> = {}
  entries.forEach(([name, tier]) => {
    rows[name] = tierRowState(tier)
  })
  return rows
}

function tierRowState(tier: TierSpec | null | undefined): TierRowState {
  return {
    provider: String(tier?.provider || ''),
    model: String(tier?.model || ''),
    thinkingLevel: String(tier?.thinkingLevel || tier?.thinking_level || ''),
    supportsImage: Boolean(tier?.supportsImage || tier?.supports_image),
  }
}
