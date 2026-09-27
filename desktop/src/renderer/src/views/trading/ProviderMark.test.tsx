import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ProviderMark, providerMark } from './ProviderMark'

describe('ProviderMark · the route wears its own logo', () => {
  it('has a mark for both providers and none for anything else', () => {
    expect(providerMark('aggregator')).toContain('<svg')
    expect(providerMark('uniswap')).toContain('<svg')
    expect(providerMark('kyber')).toBeNull()
    expect(providerMark(null)).toBeNull()
  })

  it('inlines the mark so it takes the colour of the row it sits in', () => {
    render(
      <span data-testid="seat">
        <ProviderMark id="aggregator" size={13} />
      </span>,
    )
    const svg = screen.getByTestId('seat').querySelector('svg')
    expect(svg).not.toBeNull()
    expect(svg!.innerHTML).toContain('currentColor')
  })

  it('renders nothing for a provider we have no mark for', () => {
    render(
      <span data-testid="seat">
        <ProviderMark id="kyber" />
      </span>,
    )
    expect(screen.getByTestId('seat').querySelector('svg')).toBeNull()
  })
})
