import { fireEvent, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { renderDesk } from '../test-utils'
import { MissionPicker } from './MissionPicker'
import { MISSION_PRESETS, presetById } from './presets'

describe('MissionPicker', () => {
  it('lists every preset and hands the chosen one back', () => {
    const onPick = vi.fn()
    renderDesk(<MissionPicker onPick={onPick} onCustom={vi.fn()} onClose={vi.fn()} />)
    for (const preset of MISSION_PRESETS) {
      expect(screen.getByTestId(`preset-${preset.id}`)).toBeInTheDocument()
    }
    fireEvent.click(screen.getByTestId('preset-dca'))
    expect(onPick).toHaveBeenCalledWith(presetById('dca'))
  })

  // The watch group is the safe way in, so it has to be the first thing read
  // and has to say on the card that it does not trade.
  it('puts the no-trade group first and marks its cards', () => {
    renderDesk(<MissionPicker onPick={vi.fn()} onCustom={vi.fn()} onClose={vi.fn()} />)
    const groups = screen.getByTestId('mission-picker').querySelectorAll('[data-group]')
    expect(groups[0]?.getAttribute('data-group')).toBe('watch')
    expect(screen.getByTestId('preset-portfolio-report')).toHaveTextContent('No trades')
    expect(screen.getByTestId('preset-dca')).not.toHaveTextContent('No trades')
  })

  it('offers a blank contract for anything the catalogue does not cover', () => {
    const onCustom = vi.fn()
    renderDesk(<MissionPicker onPick={vi.fn()} onCustom={onCustom} onClose={vi.fn()} />)
    fireEvent.click(screen.getByTestId('preset-custom'))
    expect(onCustom).toHaveBeenCalled()
  })
})
