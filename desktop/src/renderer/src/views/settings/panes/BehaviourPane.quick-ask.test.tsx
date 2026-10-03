import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { DesktopApi } from '@shared/ipc'
import type { QuickAskStatus } from '@shared/quick-ask'
import { DEFAULT_SETTINGS } from '@shared/settings'
import { desktopApi, resetDesktopApiForTests } from '~/lib/desktop-api'
import { useSettings } from '~/stores/settings'
import { BehaviourPane } from './BehaviourPane'
import { ShortcutsPane } from './ShortcutsPane'

/** The fallback bridge with main's hotkey status under the test's control. */
function bridge(status: QuickAskStatus) {
  resetDesktopApiForTests()
  const fallback = desktopApi()
  let push: ((s: QuickAskStatus) => void) | null = null
  window.agentos = {
    ...fallback,
    quickAsk: {
      ...fallback.quickAsk,
      status: vi.fn(async () => status),
      onStatusChanged: (listener: (s: QuickAskStatus) => void) => {
        push = listener
        return () => {
          push = null
        }
      },
    },
  } as DesktopApi
  return { push: (s: QuickAskStatus) => act(() => push?.(s)) }
}

async function mountBehaviour() {
  await act(async () => {
    await useSettings.getState().load()
  })
  await act(async () => {
    render(<BehaviourPane />)
  })
  return screen.getByRole('region', { name: 'Quick Ask' })
}

beforeEach(() => {
  delete window.agentos
  localStorage.clear()
  useSettings.setState({ settings: structuredClone(DEFAULT_SETTINGS), loaded: false })
})

describe('Settings › Behaviour › Quick Ask', () => {
  it('is on, on ⌥ Space, by default', async () => {
    bridge({ state: 'ready', shortcut: 'Alt+Space' })
    const card = await mountBehaviour()
    expect(within(card).getByRole('switch', { name: 'Global shortcut' })).toHaveAttribute(
      'aria-checked',
      'true',
    )
    expect(within(card).getByRole('radio', { name: '⌥ Space' })).toHaveAttribute(
      'aria-checked',
      'true',
    )
    expect(within(card).queryByText(/Unavailable/)).toBeNull()
  })

  it('turning it off and choosing another key write the setting', async () => {
    bridge({ state: 'ready', shortcut: 'Alt+Space' })
    const card = await mountBehaviour()
    await act(async () => {
      fireEvent.click(within(card).getByRole('radio', { name: '⌘ ⇧ Space' }))
    })
    expect(useSettings.getState().settings.quickAsk.shortcut).toBe('CommandOrControl+Shift+Space')
    await act(async () => {
      fireEvent.click(within(card).getByRole('switch', { name: 'Global shortcut' }))
    })
    expect(useSettings.getState().settings.quickAsk.enabled).toBe(false)
    // Off: the key choice is moot until it is back on.
    expect(within(card).getByRole('radio', { name: '⌘ ⇧ Space' })).toBeDisabled()
  })

  it('says so when macOS refused the key', async () => {
    const { push } = bridge({ state: 'ready', shortcut: 'Alt+Space' })
    const card = await mountBehaviour()
    push({ state: 'unavailable', shortcut: 'Alt+Space' })
    expect(within(card).getByRole('status')).toHaveTextContent(/Unavailable/)
  })

  it('lists the chosen key under App in Settings › Shortcuts', async () => {
    bridge({ state: 'ready', shortcut: 'Control+Space' })
    await act(async () => {
      await useSettings.getState().update({ quickAsk: { shortcut: 'Control+Space' } })
    })
    render(<ShortcutsPane />)
    const app = screen.getByRole('region', { name: 'App' })
    expect(within(app).getByText('Quick Ask, from any app')).toBeInTheDocument()
    expect(within(app).getByLabelText('⌃ Space')).toBeInTheDocument()
  })
})
