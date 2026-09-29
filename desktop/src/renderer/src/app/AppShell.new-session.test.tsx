import { act, fireEvent, render } from '@testing-library/react'
import { useEffect } from 'react'
import { MemoryRouter, useLocation } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { KeyboardShortcutProvider } from '@/components/KeyboardShortcuts'
import { useTradingUi } from '~/stores/trading-ui'
import { useShellShortcuts } from './AppShell'

// Issue #3519: at the desk ⌘N went to the keyless `/sessions` home, which is
// Chat mode, while ⌘⇧O, `/new` and the pen button start a fresh desk session.

const DESK_PATH = '/sessions/agent%3Atrading%3Awebchat%3Atrading-t1'

const seen = { path: '' }
function Probe() {
  useShellShortcuts()
  const { pathname } = useLocation()
  useEffect(() => {
    seen.path = pathname
  }, [pathname])
  return null
}

function renderShell(path: string) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <KeyboardShortcutProvider>
        <Probe />
      </KeyboardShortcutProvider>
    </MemoryRouter>,
  )
}

function pressNewSession() {
  act(() => {
    fireEvent.keyDown(document, { key: 'n', code: 'KeyN', metaKey: true })
  })
}

afterEach(() => {
  useTradingUi.setState({ startFreshDesk: null })
})

describe('shell ⌘N', () => {
  it('starts a fresh desk session at the desk and stays there', () => {
    const startFreshDesk = vi.fn()
    useTradingUi.setState({ startFreshDesk })
    renderShell(DESK_PATH)

    pressNewSession()

    expect(startFreshDesk).toHaveBeenCalledTimes(1)
    expect(seen.path).toBe(DESK_PATH)
  })

  it('goes to the new-chat home in Chat mode', () => {
    renderShell('/sessions/agent%3Amain%3Awebchat%3Achat-1')

    pressNewSession()

    expect(seen.path).toBe('/sessions')
  })
})
