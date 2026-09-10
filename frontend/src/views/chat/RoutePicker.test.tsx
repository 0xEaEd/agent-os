import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { RoutePicker } from './RoutePicker'
import type { RoutePinApi } from './useRoutePin'

function route(overrides: Partial<RoutePinApi> = {}): RoutePinApi {
  const base: RoutePinApi = {
    enabled: true,
    tiers: [
      { tier: 'c0', model: 'deepseek-v4-flash' },
      { tier: 'c1', model: 'gpt-5.6-luna' },
      { tier: 'c2', model: 'glm-5.2' },
      { tier: 'c3', model: 'claude-opus-5' },
    ],
    imageTiers: [{ tier: 'image_model', model: 'gpt-4o' }],
    models: [
      // gpt-5.6-luna is also tier c1's model — the overlap the dedup handles.
      { id: 'gpt-5.6-luna', name: 'gpt-5.6-luna' },
      { id: 'gpt-5.6-terra', name: 'gpt-5.6-terra' },
      { id: 'grok-5', name: 'grok-5' },
    ],
    pinned: null,
    pinnedModel: null,
    isPinned: false,
    lastRoutedTier: null,
    lastRoutedModel: null,
    imageOverride: false,
    busy: false,
    pin: vi.fn(),
    pinModel: vi.fn(),
    clear: vi.fn(),
    ...overrides,
  }
  // Derived in the hook, so derive it here too rather than making every case
  // restate it — a fixture that let the two drift would hide the very bug this
  // field exists to prevent.
  return { ...base, isPinned: base.pinned !== null || base.pinnedModel !== null }
}

const trigger = () => screen.getByRole('button', { name: 'Model route' })

describe('RoutePicker', () => {
  it('labels the pinned tier with the model it resolves to', () => {
    render(<RoutePicker route={route({ pinned: 'c1' })} />)
    expect(trigger()).toHaveTextContent('c1 · gpt-5.6-luna')
  })

  it('names the tier the router actually chose while on Auto', () => {
    render(<RoutePicker route={route({ lastRoutedTier: 'c2' })} />)
    expect(trigger()).toHaveTextContent('Auto · c2')
  })

  it('names the model the router actually used while on Auto', () => {
    render(<RoutePicker route={route({ lastRoutedTier: 'c2', lastRoutedModel: 'glm-5.2' })} />)
    expect(trigger()).toHaveTextContent('Auto · c2 · glm-5.2')
  })

  it('names the image model on an image turn, whose tier has no row to look up', () => {
    render(
      <RoutePicker route={route({ lastRoutedTier: 'image_model', lastRoutedModel: 'gpt-4o' })} />,
    )
    expect(trigger()).toHaveTextContent('Auto · image_model · gpt-4o')
  })

  it('spells the routed model out in the title, which the capped label elides', () => {
    render(<RoutePicker route={route({ lastRoutedTier: 'c2', lastRoutedModel: 'glm-5.2' })} />)
    expect(trigger()).toHaveAttribute(
      'title',
      'The Pilot Router routed the last turn to c2 · glm-5.2',
    )
  })

  it('keeps the generic Auto title until a turn has actually routed', () => {
    render(<RoutePicker route={route()} />)
    expect(trigger()).toHaveAttribute('title', 'The Pilot Router picks a tier for each turn')
  })

  it('falls back to a bare Auto before any turn has been routed', () => {
    render(<RoutePicker route={route()} />)
    expect(trigger()).toHaveTextContent('Auto')
    expect(trigger()).not.toHaveTextContent('·')
  })

  it('marks a pin as active so a standing override does not read as neutral', () => {
    render(<RoutePicker route={route({ pinned: 'c3' })} />)
    expect(trigger()).toHaveClass('chat-route-trigger--pinned')
  })

  it('disables rather than hides the control when no router is configured', () => {
    render(<RoutePicker route={route({ enabled: false })} />)
    expect(trigger()).toBeDisabled()
    expect(trigger()).toHaveAttribute('title', 'Turn on the Pilot Router to pick a tier')
  })

  it('disables the trigger while a pin round-trip is in flight', () => {
    render(<RoutePicker route={route({ busy: true })} />)
    expect(trigger()).toBeDisabled()
  })

  it('lists Auto, every pinnable tier, then the remaining models', () => {
    render(<RoutePicker route={route()} />)
    fireEvent.click(trigger())
    const options = screen.getAllByRole('option')
    // Auto + 4 tiers + terra + grok. gpt-5.6-luna is dropped: tier c1 already
    // offers that model AND carries its configured thinking level.
    expect(options).toHaveLength(7)
    expect(options[0]).toHaveTextContent('Auto')
    expect(options[2]).toHaveTextContent('c1')
    expect(options[2]).toHaveTextContent('gpt-5.6-luna')
    expect(options[6]).toHaveTextContent('grok-5')
  })

  it('does not offer a model a tier already covers', () => {
    render(<RoutePicker route={route()} />)
    fireEvent.click(trigger())
    const luna = screen.getAllByRole('option').filter((o) => o.textContent?.includes('luna'))
    expect(luna).toHaveLength(1)
    expect(luna[0]).toHaveTextContent('c1')
  })

  it('checks the option matching the active pin', () => {
    render(<RoutePicker route={route({ pinned: 'c2' })} />)
    fireEvent.click(trigger())
    const options = screen.getAllByRole('option')
    expect(options[0]).toHaveAttribute('aria-selected', 'false')
    expect(options[3]).toHaveAttribute('aria-selected', 'true')
  })

  it('checks Auto when nothing is pinned', () => {
    render(<RoutePicker route={route()} />)
    fireEvent.click(trigger())
    expect(screen.getAllByRole('option')[0]).toHaveAttribute('aria-selected', 'true')
  })

  it('pins the chosen tier and closes the menu', () => {
    const pin = vi.fn()
    render(<RoutePicker route={route({ pin })} />)
    fireEvent.click(trigger())
    fireEvent.click(screen.getAllByRole('option')[4]!)
    expect(pin).toHaveBeenCalledWith('c3')
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  })

  it('pins a directly-named model through pinModel, not pin', () => {
    const pin = vi.fn()
    const pinModel = vi.fn()
    render(<RoutePicker route={route({ pin, pinModel })} />)
    fireEvent.click(trigger())
    fireEvent.click(screen.getByRole('option', { name: /grok-5/ }))
    expect(pinModel).toHaveBeenCalledWith('grok-5')
    expect(pin).not.toHaveBeenCalled()
  })

  it('labels a model pin with the model alone — no tier was chosen', () => {
    render(<RoutePicker route={route({ pinnedModel: 'grok-5' })} />)
    expect(trigger()).toHaveTextContent('grok-5')
    expect(trigger()).toHaveClass('chat-route-trigger--pinned')
  })

  it('checks the pinned model rather than Auto', () => {
    render(<RoutePicker route={route({ pinnedModel: 'grok-5' })} />)
    fireEvent.click(trigger())
    expect(screen.getAllByRole('option')[0]).toHaveAttribute('aria-selected', 'false')
    expect(screen.getByRole('option', { name: /grok-5/ })).toHaveAttribute('aria-selected', 'true')
  })

  it('filters the list by tier or model text', () => {
    render(<RoutePicker route={route()} />)
    fireEvent.click(trigger())
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'grok' } })
    const options = screen.getAllByRole('option')
    expect(options).toHaveLength(1)
    expect(options[0]).toHaveTextContent('grok-5')
  })

  it('reports an empty filter result rather than an empty box', () => {
    render(<RoutePicker route={route()} />)
    fireEvent.click(trigger())
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'zzz' } })
    expect(screen.queryAllByRole('option')).toHaveLength(0)
    expect(screen.getByText('No route matches')).toBeInTheDocument()
  })

  it('clears the filter after a pick so the next open starts fresh', () => {
    render(<RoutePicker route={route()} />)
    fireEvent.click(trigger())
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'grok' } })
    fireEvent.click(screen.getByRole('option', { name: /grok-5/ }))
    fireEvent.click(trigger())
    expect(screen.getByRole('textbox')).toHaveValue('')
  })

  it('restores automatic routing through Auto rather than pinning it', () => {
    const pin = vi.fn()
    const clear = vi.fn()
    render(<RoutePicker route={route({ pinned: 'c3', pin, clear })} />)
    fireEvent.click(trigger())
    fireEvent.click(screen.getAllByRole('option')[0]!)
    expect(clear).toHaveBeenCalledTimes(1)
    expect(pin).not.toHaveBeenCalled()
  })

  it('reports the image tier so the vision model is knowable before sending one', () => {
    render(<RoutePicker route={route()} />)
    fireEvent.click(trigger())
    expect(screen.getByText('Images route here automatically')).toBeInTheDocument()
    expect(screen.getByText('image_model')).toBeInTheDocument()
    expect(screen.getByText('gpt-4o')).toBeInTheDocument()
  })

  it('keeps the image tier out of the options, since a pin on it never applies', () => {
    render(<RoutePicker route={route()} />)
    fireEvent.click(trigger())
    // Auto + 4 tiers + terra + grok, exactly as before: the image row is not
    // one of them.
    expect(screen.getAllByRole('option')).toHaveLength(7)
    expect(screen.queryByRole('option', { name: /image_model/ })).not.toBeInTheDocument()
  })

  it('does not repeat a vision tier that is already pinnable in the list', () => {
    // c3 takes images AND text, so it is a real, pinnable option; listing it
    // again below would read as a second, different route.
    render(
      <RoutePicker
        route={route({
          imageTiers: [
            { tier: 'c3', model: 'claude-opus-5' },
            { tier: 'image_model', model: 'gpt-4o' },
          ],
        })}
      />,
    )
    fireEvent.click(trigger())
    expect(screen.getAllByText('c3')).toHaveLength(1)
    expect(screen.getByText('image_model')).toBeInTheDocument()
  })

  it('filters the image rows with the same search as the options', () => {
    render(<RoutePicker route={route()} />)
    fireEvent.click(trigger())
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'grok' } })
    expect(screen.queryByText('image_model')).not.toBeInTheDocument()
    expect(screen.queryByText('Images route here automatically')).not.toBeInTheDocument()
  })

  it('finds the image tier by its model id, which no option row carries', () => {
    render(<RoutePicker route={route()} />)
    fireEvent.click(trigger())
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'gpt-4o' } })
    expect(screen.queryByRole('option')).not.toBeInTheDocument()
    expect(screen.getByText('image_model')).toBeInTheDocument()
    // The image row IS the match, so the empty-list note would contradict it.
    expect(screen.queryByText('No route matches')).not.toBeInTheDocument()
  })

  it('still reports no match when neither an option nor an image row survives', () => {
    render(<RoutePicker route={route()} />)
    fireEvent.click(trigger())
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'nothing-here' } })
    expect(screen.getByText('No route matches')).toBeInTheDocument()
  })

  it('omits the section entirely when no tier takes images', () => {
    render(<RoutePicker route={route({ imageTiers: [] })} />)
    fireEvent.click(trigger())
    expect(screen.queryByText('Images route here automatically')).not.toBeInTheDocument()
  })

  it('flags the turns an image route took instead of the pin', () => {
    render(<RoutePicker route={route({ pinned: 'c0', imageOverride: true })} />)
    expect(screen.getByText('image route')).toBeInTheDocument()
  })

  it('shows no override badge on ordinary text turns', () => {
    render(<RoutePicker route={route({ pinned: 'c0' })} />)
    expect(screen.queryByText('image route')).not.toBeInTheDocument()
  })

  it('shows no override badge on an image turn that overrode nothing', () => {
    // Auto routing: the hook reports no override, so the label carries the
    // image route on its own rather than a badge claiming a bypassed pin.
    render(
      <RoutePicker route={route({ lastRoutedTier: 'image_model', lastRoutedModel: 'gpt-4o' })} />,
    )
    expect(screen.queryByText('image route')).not.toBeInTheDocument()
    expect(trigger()).toHaveTextContent('image_model · gpt-4o')
  })

  it('closes on Escape without reaching the composer abort chain', () => {
    render(<RoutePicker route={route()} />)
    fireEvent.click(trigger())
    expect(screen.getByRole('listbox')).toBeInTheDocument()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  })

  it('closes on an outside mousedown', () => {
    render(<RoutePicker route={route()} />)
    fireEvent.click(trigger())
    fireEvent.mouseDown(document.body)
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  })
})
