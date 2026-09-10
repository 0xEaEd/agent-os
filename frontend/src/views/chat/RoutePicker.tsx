import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { CheckIcon, RouteIcon } from 'lucide-react'
import type { RoutePinApi, RoutePinTier } from './useRoutePin'
import { t } from '@/i18n'
import '@/i18n/en/chat'

/**
 * The composer's route picker: hand routing to the Pilot Router, pin one of the
 * configured tiers, or name a model directly.
 *
 * The three live in ONE searchable list because they answer one question —
 * "what answers the next turn?" — even though they are different mechanisms
 * underneath. Tiers carry settings (thinking level, pricing baseline) that a
 * bare model id does not; a directly-named model borrows them from the default
 * tier. That distinction matters to the router, not to the person choosing, so
 * it stays out of the list and lives in the tier rows' own labels.
 *
 * The vision tiers sit BELOW that list, outside the listbox, as plain text. An
 * image turn is routed before holds are consulted, so those tiers are not
 * choices — offering them as options would promise a pin that the router
 * ignores. They are shown at all because otherwise the only way to learn what
 * an image is handed to is to send one and read the label afterwards.
 *
 * The button reports what is actually in force:
 *
 *   - tier pinned  → `c1 · gpt-5.6-luna`
 *   - model pinned → the model id alone; no tier was chosen
 *   - auto         → `Auto · c2 · glm-5.2`, the tier the router last picked and
 *                    the model it resolved to, so "let it decide" is still
 *                    legible. The model comes from the routing decision, not
 *                    from the tier rows: an image turn names `image_model`,
 *                    which is not a pinnable tier and therefore has no row to
 *                    look up.
 *   - image turn   → an override note WHEN A PIN IS SET, because an image turn
 *                    is routed to a vision tier before pins are consulted; the
 *                    pin did not run that turn and saying otherwise would
 *                    misreport the bill. On auto there is no pin to override,
 *                    so the label alone tells the story.
 *
 * Disabled (not hidden) when no Pilot Router is configured, so the composer
 * does not reflow when the router is toggled.
 */

export interface RoutePickerProps {
  route: RoutePinApi
}

/**
 * One menu row, flattened for display. `kind` + `value` are all the click
 * handler needs; `primary`/`secondary` are what the row shows. Keeping the
 * presentation resolved here rather than branching inside the JSX keeps the
 * three row types rendering through one path.
 */
interface Row {
  kind: 'auto' | 'tier' | 'model'
  value: string
  key: string
  primary: string
  secondary: string
}

export function RoutePicker({ route }: RoutePickerProps) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const wrapRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const searchRef = useRef<HTMLInputElement>(null)

  // Close on outside click / Escape, mirroring the composer toolbar popover.
  // `mousedown` (not `click`) so it lands before the trigger's own toggle.
  useEffect(() => {
    if (!open) return
    searchRef.current?.focus()
    const onDocMouseDown = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      // The open menu owns Escape before the composer's abort/clear chain.
      e.preventDefault()
      e.stopPropagation()
      setOpen(false)
      triggerRef.current?.focus()
    }
    document.addEventListener('mousedown', onDocMouseDown)
    document.addEventListener('keydown', onKeyDown, true)
    return () => {
      document.removeEventListener('mousedown', onDocMouseDown)
      document.removeEventListener('keydown', onKeyDown, true)
    }
  }, [open])

  // Bumped when a row is picked, to hand focus back to the trigger. The focus
  // itself happens in the effect below rather than in the handler: a callback
  // that reads a ref and is invoked from JSX trips the refs lint, and routing
  // it through state also keeps the restore scoped to a deliberate SELECTION —
  // closing by clicking elsewhere must not yank focus back out of wherever the
  // user just clicked.
  const [refocus, setRefocus] = useState(0)
  useEffect(() => {
    if (refocus === 0) return
    triggerRef.current?.focus()
  }, [refocus])

  const choose = useCallback(
    (kind: Row['kind'], value: string) => {
      setOpen(false)
      setQuery('')
      setRefocus((n) => n + 1)
      if (kind === 'auto') route.clear()
      else if (kind === 'tier') route.pin(value)
      else route.pinModel(value)
    },
    [route],
  )

  const rows = useMemo<Row[]>(() => {
    const tierRows: Row[] = route.tiers.map((row) => ({
      kind: 'tier',
      value: row.tier,
      key: `t:${row.tier}`,
      primary: row.tier,
      secondary: row.model,
    }))
    // Models already offered as a tier are dropped: the tier row pins the same
    // model AND carries its configured thinking level, so it is strictly the
    // better of two rows that would otherwise look identical.
    const tierModels = new Set(route.tiers.map((row) => row.model).filter(Boolean))
    const modelRows: Row[] = route.models
      .filter((model) => !tierModels.has(model.id))
      .map((model) => ({
        kind: 'model',
        value: model.id,
        key: `m:${model.id}`,
        primary: model.name,
        secondary: '',
      }))
    const all: Row[] = [
      {
        kind: 'auto',
        value: '',
        key: 'auto',
        primary: t('chat.routeAuto'),
        secondary: t('chat.routeAutoHint'),
      },
      ...tierRows,
      ...modelRows,
    ]
    const needle = query.trim().toLowerCase()
    if (!needle) return all
    return all.filter(
      (row) =>
        row.primary.toLowerCase().includes(needle) || row.secondary.toLowerCase().includes(needle),
    )
  }, [route.tiers, route.models, query])

  // Vision tiers already offered above are dropped: a text tier that also takes
  // images is pinnable and has a real row, so repeating it here would read as a
  // second, different route. Filtered by the same needle as the options, so a
  // search does not leave a stale row stranded under an empty list.
  const imageRows = useMemo<RoutePinTier[]>(() => {
    const pinnable = new Set(route.tiers.map((row) => row.tier))
    const shown = route.imageTiers.filter((row) => !pinnable.has(row.tier))
    const needle = query.trim().toLowerCase()
    if (!needle) return shown
    return shown.filter(
      (row) => row.tier.toLowerCase().includes(needle) || row.model.toLowerCase().includes(needle),
    )
  }, [route.tiers, route.imageTiers, query])

  const selectedKey =
    route.pinnedModel !== null
      ? `m:${route.pinnedModel}`
      : route.pinned !== null
        ? `t:${route.pinned}`
        : 'auto'

  const pinnedModelLabel = route.pinned
    ? route.tiers.find((row) => row.tier === route.pinned)?.model || ''
    : ''
  const autoLabel = !route.lastRoutedTier
    ? t('chat.routeAuto')
    : route.lastRoutedModel
      ? t('chat.routeAutoWithTierModel', {
          tier: route.lastRoutedTier,
          model: route.lastRoutedModel,
        })
      : t('chat.routeAutoWithTier', { tier: route.lastRoutedTier })
  const label = route.pinnedModel
    ? route.pinnedModel
    : route.pinned
      ? pinnedModelLabel
        ? `${route.pinned} · ${pinnedModelLabel}`
        : route.pinned
      : autoLabel

  // The trigger is width-capped and ellipsises, so a long model id is readable
  // only here — the auto title repeats the route in full rather than restating
  // the generic "the router picks a tier" once a turn has actually routed.
  const autoTitle =
    route.lastRoutedTier && route.lastRoutedModel
      ? t('chat.routeAutoRoutedTitle', {
          tier: route.lastRoutedTier,
          model: route.lastRoutedModel,
        })
      : t('chat.routeAutoTitle')
  const title = !route.enabled
    ? t('chat.routeDisabledTitle')
    : route.pinnedModel
      ? t('chat.routeModelPinnedTitle', { model: route.pinnedModel })
      : route.pinned
        ? t('chat.routePinnedTitle', { tier: route.pinned })
        : autoTitle

  return (
    <div className="chat-route-wrap" ref={wrapRef}>
      <button
        ref={triggerRef}
        type="button"
        className={
          route.isPinned ? 'chat-route-trigger chat-route-trigger--pinned' : 'chat-route-trigger'
        }
        disabled={!route.enabled || route.busy}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls="chat-route-menu"
        aria-label={t('chat.routeLabel')}
        title={title}
        onClick={() => setOpen((v) => !v)}
      >
        <RouteIcon aria-hidden="true" />
        <span className="chat-route-trigger__label">{label}</span>
      </button>
      {route.imageOverride ? (
        <span className="chat-route-badge" title={t('chat.routeImageOverrideTitle')}>
          {t('chat.routeImageOverride')}
        </span>
      ) : null}
      {open ? (
        <div className="chat-route-menu">
          <input
            ref={searchRef}
            type="text"
            className="chat-route-search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t('chat.routeSearchPlaceholder')}
            aria-label={t('chat.routeSearchPlaceholder')}
          />
          <ul id="chat-route-menu" className="chat-route-list" role="listbox">
            {rows.length === 0 ? (
              imageRows.length === 0 ? (
                <li className="chat-route-empty">{t('chat.routeNoMatch')}</li>
              ) : null
            ) : (
              rows.map((row) => (
                <li role="none" key={row.key}>
                  <button
                    type="button"
                    role="option"
                    aria-selected={row.key === selectedKey}
                    className="chat-route-option"
                    onClick={() => choose(row.kind, row.value)}
                  >
                    <span className="chat-route-option__check" aria-hidden="true">
                      {row.key === selectedKey ? <CheckIcon /> : null}
                    </span>
                    <span className="chat-route-option__tier">{row.primary}</span>
                    <span className="chat-route-option__model">{row.secondary}</span>
                  </button>
                </li>
              ))
            )}
          </ul>
          {imageRows.length > 0 ? (
            <div className="chat-route-image" title={t('chat.routeImageHintTitle')}>
              <p className="chat-route-image__hint">{t('chat.routeImageHint')}</p>
              {imageRows.map((row) => (
                <p className="chat-route-image__row" key={row.tier}>
                  <span className="chat-route-image__tier">{row.tier}</span>
                  <span className="chat-route-image__model">{row.model}</span>
                </p>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
