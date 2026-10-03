import './quick-ask.css'
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { QUICK_ASK_MAX_BYTES, utf8Length, type QuickAskTarget } from '@shared/quick-ask'
import { t } from '~/i18n'
import { desktopApi } from '~/lib/desktop-api'
import { useSettings } from '~/stores/settings'
import { syncThemeFromSettings } from '~/theme/theme-store'

/** The hash route the panel window loads (main/quick-ask/panel.ts QUICK_ASK_ROUTE). */
export const QUICK_ASK_HASH = '#/quick-ask'

/** Is this renderer the Quick Ask panel rather than the main window? */
export function isQuickAskWindow(hash: string = window.location.hash): boolean {
  return hash === QUICK_ASK_HASH || hash.startsWith(`${QUICK_ASK_HASH}?`)
}

/**
 * The Quick Ask panel: one field over whatever app is in front. Return sends
 * the text to a new chat, Option-Return to the chat the main window is on,
 * Escape closes. The text goes to main over IPC and on to the main window;
 * this window has no gateway connection, no sidebar and no toolbar.
 *
 * Escape (or clicking away) keeps what was typed, so an accidental close is
 * not a loss; a send clears it.
 */
export function QuickAskView() {
  const [text, setText] = useState('')
  const [refused, setRefused] = useState(false)
  const fieldRef = useRef<HTMLTextAreaElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)
  const sending = useRef(false)
  const tooLong = utf8Length(text) > QUICK_ASK_MAX_BYTES

  // The window's own CSS context: transparent page, nothing from the shell.
  useLayoutEffect(() => {
    const root = document.documentElement
    root.dataset.window = 'quick-ask'
    return () => {
      delete root.dataset.window
    }
  }, [])

  const focusField = useCallback(() => {
    const field = fieldRef.current
    if (!field) return
    field.focus()
    const end = field.value.length
    field.setSelectionRange(end, end)
  }, [])

  // Mounted: read the appearance settings, then tell main the panel may show.
  useEffect(() => {
    const api = desktopApi()
    void useSettings
      .getState()
      .load()
      .catch(() => {})
    void api.quickAsk.ready()
  }, [])

  // Every time it appears: the field has the keyboard, and the look matches
  // the main window's (theme, palette or transparency may have changed while
  // the panel was hidden; this window does not hear those writes itself).
  useEffect(
    () =>
      desktopApi().quickAsk.onShown(() => {
        focusField()
        void useSettings
          .getState()
          .load()
          .then(() => syncThemeFromSettings(useSettings.getState().settings.theme))
          .catch(() => {})
      }),
    [focusField],
  )

  // The window is exactly as tall as the panel: report every change in height.
  useEffect(() => {
    const panel = panelRef.current
    if (!panel || typeof ResizeObserver === 'undefined') return
    let last = 0
    const report = () => {
      const height = Math.ceil(panel.getBoundingClientRect().height)
      if (height > 0 && height !== last) {
        last = height
        void desktopApi().quickAsk.resize(height)
      }
    }
    const observer = new ResizeObserver(report)
    observer.observe(panel)
    report()
    return () => observer.disconnect()
  }, [])

  const submit = useCallback(
    async (target: QuickAskTarget) => {
      if (sending.current || !text.trim() || tooLong) return
      sending.current = true
      try {
        const ok = await desktopApi().quickAsk.submit({ text, target })
        setRefused(!ok)
        if (ok) setText('')
      } catch {
        setRefused(true)
      } finally {
        sending.current = false
      }
    },
    [text, tooLong],
  )

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      // An input method mid-composition owns Return and Escape.
      if (e.nativeEvent.isComposing || e.keyCode === 229) return
      if (e.key === 'Escape') {
        e.preventDefault()
        void desktopApi().quickAsk.hide()
        return
      }
      if (e.key === 'Enter' && !e.shiftKey && !e.metaKey && !e.ctrlKey) {
        e.preventDefault()
        void submit(e.altKey ? 'current' : 'new')
      }
    },
    [submit],
  )

  const notice = tooLong ? t('quickAsk.tooLong') : refused ? t('quickAsk.refused') : null

  return (
    <div className="qa-root">
      <div ref={panelRef} className="qa-panel" data-testid="quick-ask">
        <div className="qa-row">
          <AgentOsGlyph />
          <textarea
            ref={fieldRef}
            className="qa-field"
            rows={1}
            value={text}
            autoFocus
            spellCheck={false}
            aria-label={t('quickAsk.field')}
            placeholder={t('quickAsk.placeholder')}
            onChange={(e) => {
              setText(e.target.value)
              if (refused) setRefused(false)
            }}
            onKeyDown={onKeyDown}
          />
        </div>
        <div className="qa-hint" data-tone={notice ? 'warn' : undefined} aria-live="polite">
          {notice ?? (
            <>
              <kbd>↩</kbd>
              <span>{t('quickAsk.hint.new')}</span>
              <span className="qa-hint__sep" aria-hidden>
                ·
              </span>
              <kbd>⌥↩</kbd>
              <span>{t('quickAsk.hint.current')}</span>
              <span className="qa-hint__sep" aria-hidden>
                ·
              </span>
              <kbd>esc</kbd>
              <span>{t('quickAsk.hint.close')}</span>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

/** The AgentOS mark (assets/providers/agentos.svg), drawn in the accent. */
function AgentOsGlyph() {
  return (
    <svg className="qa-glyph" viewBox="0 0 24 24" fill="none" aria-hidden>
      <g stroke="currentColor" strokeWidth="1.2" strokeLinecap="round">
        <path d="M12 14.3V4.2" />
        <path d="M12 14.3 2.7 19.9" />
        <path d="M12 14.3l9.3 5.6" />
      </g>
      <g fill="currentColor">
        <circle cx="12" cy="14.3" r="3.15" />
        <circle cx="12" cy="4.2" r="1.7" />
        <circle cx="2.7" cy="19.9" r="1.7" />
        <circle cx="21.3" cy="19.9" r="1.7" />
      </g>
    </svg>
  )
}
