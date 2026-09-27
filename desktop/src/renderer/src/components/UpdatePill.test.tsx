import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useConnection } from '@/stores/connection'
import { IDLE_ENGINE, idleAppState } from '@shared/updates'
import { useUi } from '~/stores/ui'
import { useUpdates } from '~/stores/updates'
import { UpdatePill } from './UpdatePill'

const rpcCall = vi.fn(async () => ({ version: '2026.9.22' }))
vi.mock('@/app/providers', () => ({ useRpc: () => ({ call: rpcCall }) }))

const base = idleAppState('2026.9.22')

function renderPill() {
  return render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <UpdatePill />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  useConnection.getState().setState('disconnected')
  useUpdates.setState({ engine: { ...IDLE_ENGINE }, app: { ...base } })
})

describe('UpdatePill', () => {
  it('stays hidden while there is nothing to do', () => {
    renderPill()
    expect(screen.queryByRole('button')).toBeNull()
    useUpdates.setState({
      engine: { ...IDLE_ENGINE, availability: 'up-to-date' },
      app: { ...base, phase: 'up-to-date' },
    })
    renderPill()
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('offers the release when only the engine is outdated, and opens About', () => {
    const openSettings = vi.fn()
    useUi.setState({ openSettings })
    useUpdates.setState({
      engine: { ...IDLE_ENGINE, availability: 'outdated', latest: '2026.9.23' },
    })
    renderPill()
    fireEvent.click(screen.getByRole('button', { name: /Update · AgentOS 2026.9.23/ }))
    expect(openSettings).toHaveBeenCalledWith('about')
  })

  it('stays up as "Update failed" after a broken download', () => {
    useUpdates.setState({
      app: { ...base, phase: 'error', latest: '2026.9.23', error: 'net::ERR_CONNECTION_RESET' },
    })
    renderPill()
    expect(
      screen.getByRole('button', { name: /Update failed · AgentOS 2026.9.23/ }),
    ).toHaveAttribute('data-kind', 'failed')
  })

  it('walks engine install → app download → Restart', () => {
    useUpdates.setState({
      engine: { ...IDLE_ENGINE, phase: 'installing', latest: '2026.9.23' },
      app: { ...base, phase: 'available', latest: '2026.9.23' },
    })
    const view = renderPill()
    expect(screen.getByRole('button', { name: /Updating engine/ })).toHaveAttribute(
      'data-busy',
      'true',
    )

    useUpdates.setState({
      engine: { ...IDLE_ENGINE, phase: 'done' },
      app: { ...base, phase: 'downloading', latest: '2026.9.23', percent: 37 },
    })
    view.rerender(
      <QueryClientProvider client={new QueryClient()}>
        <UpdatePill />
      </QueryClientProvider>,
    )
    expect(screen.getByText('37%')).toBeInTheDocument()

    useUpdates.setState({
      app: { ...base, phase: 'downloaded', latest: '2026.9.23', percent: 100 },
    })
    view.rerender(
      <QueryClientProvider client={new QueryClient()}>
        <UpdatePill />
      </QueryClientProvider>,
    )
    expect(screen.getByRole('button', { name: /Restart · AgentOS 2026.9.23/ })).toHaveAttribute(
      'data-ready',
      'true',
    )
  })
})
