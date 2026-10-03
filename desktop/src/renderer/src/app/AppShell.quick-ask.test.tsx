import { act, render } from '@testing-library/react'
import { useEffect } from 'react'
import { MemoryRouter, useLocation } from 'react-router'
import { beforeEach, describe, expect, it } from 'vitest'
import { DEFAULT_SETTINGS } from '@shared/settings'
import { useQuickAsk } from '~/stores/quick-ask'
import { useSettings } from '~/stores/settings'
import { useLaunchView } from './AppShell'

// "Open at launch: Last session" must not pull a window that was opened for a
// Quick Ask (#3594) away from the fresh session the submission is going to.
const LAST = 'agent:main:webchat:last-one'

const seen = { path: '' }
function Probe() {
  useLaunchView()
  const { pathname } = useLocation()
  useEffect(() => {
    seen.path = pathname
  }, [pathname])
  return null
}

async function launch() {
  render(
    <MemoryRouter initialEntries={['/sessions']}>
      <Probe />
    </MemoryRouter>,
  )
  await act(async () => {
    useSettings.setState({
      settings: {
        ...structuredClone(DEFAULT_SETTINGS),
        general: { ...DEFAULT_SETTINGS.general, launchView: 'last' },
      },
      loaded: true,
    })
  })
}

beforeEach(() => {
  localStorage.clear()
  localStorage.setItem('agentos-desktop.lastSession', LAST)
  useSettings.setState({ settings: structuredClone(DEFAULT_SETTINGS), loaded: false })
  useQuickAsk.setState({ queue: [] })
  seen.path = ''
})

describe('useLaunchView with Quick Ask', () => {
  it('opens the last session at launch as usual', async () => {
    await launch()
    expect(seen.path).toBe(`/sessions/${encodeURIComponent(LAST)}`)
  })

  it('stays put when the window was opened for a Quick Ask', async () => {
    useQuickAsk.getState().receive([{ text: 'hi', target: 'new' }])
    await launch()
    expect(seen.path).toBe('/sessions')
  })
})
