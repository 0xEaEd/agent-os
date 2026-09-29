import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useConnection } from '@/stores/connection'
import { useUi } from '~/stores/ui'
import { Toolbar } from './Toolbar'

vi.mock('@/app/providers', () => ({ useRpc: () => ({ call: vi.fn(async () => ({})) }) }))

beforeEach(() => {
  useConnection.getState().setState('disconnected')
  useUi.setState({ sidebarOpen: true })
})

function renderToolbar() {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <Toolbar />
    </QueryClientProvider>,
  )
}

/** Every button that shows or hides the sidebar, whatever its label says. */
function sidebarToggles() {
  return screen.queryAllByRole('button', { name: /^(Hide|Show) sidebar$/ })
}

describe('Toolbar', () => {
  it('has no Inspector button: there is no right-side panel for it to open', () => {
    renderToolbar()
    expect(screen.queryByRole('button', { name: 'Inspector' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Settings' })).toBeInTheDocument()
  })

  it('renders one sidebar toggle, on the left, when the sidebar is open', () => {
    renderToolbar()
    expect(sidebarToggles()).toHaveLength(1)
    const toggle = screen.getByRole('button', { name: 'Hide sidebar' })
    expect(toggle).toHaveAttribute('title', 'Hide sidebar (⌘⇧S)')
    // The right cluster is the one holding Settings; the toggle is not in it.
    const rightCluster = screen.getByRole('button', { name: 'Settings' }).parentElement!
    expect(rightCluster).not.toContainElement(toggle)
  })

  it('renders one sidebar toggle, on the left, when the sidebar is collapsed', () => {
    useUi.setState({ sidebarOpen: false })
    renderToolbar()
    expect(sidebarToggles()).toHaveLength(1)
    const toggle = screen.getByRole('button', { name: 'Show sidebar' })
    expect(toggle).toHaveAttribute('title', 'Show sidebar (⌘⇧S)')
    const rightCluster = screen.getByRole('button', { name: 'Settings' }).parentElement!
    expect(rightCluster).not.toContainElement(toggle)
  })

  it('toggles the sidebar and relabels itself to the next action', () => {
    renderToolbar()
    fireEvent.click(screen.getByRole('button', { name: 'Hide sidebar' }))
    expect(useUi.getState().sidebarOpen).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: 'Show sidebar' }))
    expect(useUi.getState().sidebarOpen).toBe(true)
    expect(sidebarToggles()).toHaveLength(1)
  })
})
