import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useSessionMarks } from '~/stores/session-marks'
import { useSessionSelection } from '~/stores/session-selection'
import { useUi } from '~/stores/ui'
import { SessionList } from './SessionList'

const rpcCall = vi.fn(async () => ({}))
const rpc = { call: rpcCall, waitForConnection: async () => {}, on: () => () => {} }
vi.mock('@/app/providers', () => ({ useRpc: () => rpc }))

const NOW = Date.now()

function raw(key: string, title: string, project?: string) {
  return {
    key,
    derived_title: title,
    updated_at: NOW,
    ...(project ? { project_id: project } : {}),
  }
}

/**
 * The list as the gateway would return it, seeded into the query cache: with
 * no connection the queries stay idle and SessionList renders these.
 * Folder "Roadmap": [f1, f2]. Loose: [a, b].
 */
function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity, staleTime: Infinity } },
  })
  client.setQueryData(['projects'], {
    projects: [{ project_id: 'p1', name: 'Roadmap', agent_id: 'main' }],
  })
  client.setQueryData(['sessions'], {
    sessions: [
      raw('k:f1', 'Folder one', 'p1'),
      raw('k:f2', 'Folder two', 'p1'),
      raw('k:a', 'Loose A'),
      raw('k:b', 'Loose B'),
    ],
  })
  render(
    <MemoryRouter initialEntries={['/sessions']}>
      <QueryClientProvider client={client}>
        <SessionList />
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

/** A row by its title (the link's name also carries the age). */
function link(title: string) {
  return screen.getByTitle(title)
}

function selected(): string[] {
  return [...useSessionSelection.getState().keys].sort()
}

beforeEach(() => {
  localStorage.clear()
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  useUi.setState({ openFolders: new Set(['p1']), sessionQuery: '', creatingProject: false })
  useSessionMarks.setState({ pinned: new Set(), archived: new Set(), unread: new Set() })
  useSessionSelection.setState({
    visible: [],
    order: [],
    keys: new Set(),
    anchor: null,
    pendingDelete: null,
  })
})

describe('SessionList: what a selection can reach', () => {
  it('Shift-click ranges from a chat in an open folder into the loose list', () => {
    mount()
    fireEvent.click(link('Folder two'), { metaKey: true })
    fireEvent.click(link('Loose B'), { shiftKey: true })
    expect(selected()).toEqual(['k:a', 'k:b', 'k:f2'])
  })

  it('closing the folder deselects the chats it hides', () => {
    mount()
    fireEvent.click(link('Folder one'), { metaKey: true })
    fireEvent.click(link('Loose A'), { metaKey: true })
    act(() => useUi.getState().toggleFolder('p1'))
    expect(selected()).toEqual(['k:a'])
  })

  it('a search that hides a selected row deselects it', () => {
    mount()
    fireEvent.click(link('Loose A'), { metaKey: true })
    fireEvent.click(link('Loose B'), { metaKey: true })
    act(() => useUi.getState().setSessionQuery('Loose B'))
    expect(selected()).toEqual(['k:b'])
  })

  it('offers the batch delete from the list, once, for the whole selection', () => {
    mount()
    fireEvent.click(link('Folder one'), { metaKey: true })
    fireEvent.click(link('Loose A'), { metaKey: true })
    fireEvent.contextMenu(link('Loose A'))
    fireEvent.click(screen.getByRole('menuitem', { name: 'Delete 2 sessions…' }))
    expect(screen.getAllByRole('alertdialog')).toHaveLength(1)
    expect(screen.getByRole('alertdialog', { name: 'Delete 2 sessions?' })).toBeInTheDocument()
  })
})
