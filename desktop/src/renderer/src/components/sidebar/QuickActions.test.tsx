import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { describe, expect, it } from 'vitest'
import { QUICK_ACTIONS, QuickActions } from './QuickActions'

describe('QuickActions', () => {
  it('has no Trading entry: trading is a mode of the chat', () => {
    expect(QUICK_ACTIONS.some((a) => a.to === '/trading')).toBe(false)
    render(
      <MemoryRouter>
        <QuickActions />
      </MemoryRouter>,
    )
    expect(screen.queryByText('Trading')).toBeNull()
    expect(screen.getByText('New session')).toBeInTheDocument()
  })
})
