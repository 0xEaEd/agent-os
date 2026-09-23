import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { toast } from 'sonner'
import { RouterSection } from './RouterSection'
import type { Catalog } from './logic'

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() },
}))

// `models.list` is the live half of the tier picker (#142). Tests that care
// about it set `modelsResponse`; the rest run on the offline catalog alone,
// which is what a gateway with no provider key configured actually serves.
let modelsResponse: unknown = []
const mockRpc = {
  waitForConnection: vi.fn().mockResolvedValue(undefined),
  // Mirrors rpc_models.py:58-66: the SERVER applies both filters, so a test
  // that returned the same rows for `capabilities: ['vision']` would prove
  // nothing about the image row.
  call: vi.fn((method: string, params?: { capabilities?: string[] }) => {
    if (method !== 'models.list') return Promise.resolve({})
    if (!Array.isArray(modelsResponse)) return Promise.resolve(modelsResponse)
    const required = params?.capabilities
    if (!required?.length) return Promise.resolve(modelsResponse)
    return Promise.resolve(
      (modelsResponse as Array<{ capabilities?: string[] }>).filter((model) =>
        required.every((capability) => (model.capabilities || []).includes(capability)),
      ),
    )
  }),
}
vi.mock('@/app/providers', () => ({ useRpc: () => mockRpc }))

const STATUS = {
  hasConfig: true,
  llmConfigured: true,
}

const CONFIG = {
  llm: { provider: 'openai', model: 'gpt-4o' },
  agentos_router: { enabled: true, strategy: 'pilot-v1', default_tier: 'c1' },
}

function catalogWithTiers(tiers: Record<string, Record<string, unknown>>): Catalog {
  return {
    routerProfiles: {
      defaultTier: 'c1',
      profiles: [
        {
          // Keep the production gateway profile shape, including fields the
          // editor does not consume.
          profileId: 'openai',
          providerId: 'openai',
          label: 'OpenAI',
          tiers,
        },
      ],
      judge: {
        profiles: {
          openai: { autoModel: 'gpt-4o-mini', models: ['gpt-4o-mini', 'gpt-4o'] },
        },
      },
    },
  }
}

function renderSection(catalog: Catalog, onSave = vi.fn()) {
  const props = {
    catalog,
    status: STATUS,
    config: CONFIG,
    onSave,
    onBack: vi.fn(),
    onNext: vi.fn(),
    saving: false,
  }
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const wrap = (node: React.ReactNode) => (
    <QueryClientProvider client={client}>{node}</QueryClientProvider>
  )
  const result = render(wrap(<RouterSection {...props} />))
  return {
    ...result,
    rerenderCatalog: (nextCatalog: Catalog) =>
      result.rerender(wrap(<RouterSection {...props} catalog={nextCatalog} />)),
  }
}

describe('RouterSection', () => {
  it('seeds tiers that appear in a later partial-catalog update without crashing', () => {
    const partialCatalog = catalogWithTiers({
      c0: { provider: 'openai', model: 'gpt-4o-mini' },
    })
    const view = renderSection(partialCatalog)

    expect(screen.getByLabelText('c0 model')).toHaveValue('gpt-4o-mini')
    expect(screen.queryByLabelText('c1 provider')).not.toBeInTheDocument()

    view.rerenderCatalog(
      catalogWithTiers({
        c0: { provider: 'openai', model: 'gpt-4o-mini' },
        c1: { provider: 'openai', model: 'gpt-4o' },
        image_model: { provider: 'openai', model: 'gpt-image-1' },
      }),
    )

    // The provider cell is a read-only chip now (#142), not an input.
    expect(screen.getByLabelText('c1 provider')).toHaveTextContent('openai')
    expect(screen.getByLabelText('c1 model')).toHaveValue('gpt-4o')
    expect(screen.getByLabelText('image_model model')).toHaveValue('gpt-image-1')
  })

  it('uses newly visible tier defaults when saving and keeps existing edits', () => {
    const onSave = vi.fn()
    const view = renderSection(
      catalogWithTiers({
        c0: { provider: 'openai', model: 'gpt-4o-mini' },
      }),
      onSave,
    )
    fireEvent.change(screen.getByLabelText('c0 model'), { target: { value: 'edited-c0' } })

    view.rerenderCatalog(
      catalogWithTiers({
        c0: { provider: 'openai', model: 'gpt-4o-mini' },
        c1: { provider: 'openai', model: 'gpt-4o' },
      }),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Save Router' }))

    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({
        tiers: expect.objectContaining({
          c0: expect.objectContaining({ model: 'edited-c0' }),
          c1: expect.objectContaining({ provider: 'openai', model: 'gpt-4o' }),
        }),
      }),
    )
  })

  it('renders unified selects and accessible image capability controls', () => {
    renderSection(
      catalogWithTiers({
        c0: { provider: 'openai', model: 'gpt-4o-mini' },
        image_model: { provider: 'openai', model: 'gpt-image-1' },
      }),
    )

    expect(screen.getByLabelText('Router mode').parentElement).toHaveClass('setup-select')
    expect(screen.getAllByRole('columnheader')).toHaveLength(5)

    const capability = screen.getByLabelText('c0 supports image')
    expect(capability).not.toBeChecked()
    expect(capability).toHaveClass('setup-check__input')
    fireEvent.click(capability)
    expect(capability).toBeChecked()

    expect(screen.getByLabelText('image_model supports image')).toBeChecked()
    expect(screen.getByLabelText('image_model supports image')).toBeDisabled()
  })
})

// ── Jev cloud classifier mode ───────────────────────────────────────────────

describe('RouterSection jev mode', () => {
  const tiers = { c0: { provider: 'openai', model: 'gpt-4o-mini' } }

  it('selecting jev shows the key, consent hint and threshold; hides judge/pilot fields', () => {
    renderSection(catalogWithTiers(tiers))
    // Pilot is the persisted strategy: its threshold is visible, jev is not.
    expect(screen.getByLabelText('Pilot safety-net threshold')).toBeInTheDocument()
    expect(screen.queryByLabelText('TypeSafe API key')).not.toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Router mode'), { target: { value: 'jev' } })

    const key = screen.getByLabelText('TypeSafe API key')
    expect(key).toHaveAttribute('type', 'password')
    expect(key).toHaveAttribute('autocomplete', 'off')
    expect(key).toHaveAttribute('placeholder', expect.stringContaining('TYPESAFE_API_KEY'))
    expect(screen.getByText(/Sends the current turn text to typesafe\.ai/)).toBeInTheDocument()
    const threshold = screen.getByLabelText('High-risk floor threshold')
    expect(threshold).toHaveValue(0.7)
    expect(threshold).toHaveAttribute('min', '0')
    expect(threshold).toHaveAttribute('max', '1')
    expect(threshold).toHaveAttribute('step', '0.05')
    expect(screen.queryByLabelText('Pilot safety-net threshold')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Judge model')).not.toBeInTheDocument()
  })

  it('saving jev sends the strategy, key and threshold', () => {
    const onSave = vi.fn()
    renderSection(catalogWithTiers(tiers), onSave)
    fireEvent.change(screen.getByLabelText('Router mode'), { target: { value: 'jev' } })
    fireEvent.change(screen.getByLabelText('TypeSafe API key'), { target: { value: 'k' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save Router' }))

    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({
        mode: 'recommended',
        strategy: 'jev',
        jevApiKey: 'k',
        jevHighRiskThreshold: 0.7,
      }),
    )
    expect(onSave.mock.calls[0]?.[0]?.safetyNetThreshold).toBeUndefined()
  })

  it('a blank key is sent as null so the persisted / env key is preserved', () => {
    const onSave = vi.fn()
    renderSection(catalogWithTiers(tiers), onSave)
    fireEvent.change(screen.getByLabelText('Router mode'), { target: { value: 'jev' } })
    fireEvent.change(screen.getByLabelText('High-risk floor threshold'), {
      target: { value: '0.85' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save Router' }))

    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({ strategy: 'jev', jevApiKey: null, jevHighRiskThreshold: 0.85 }),
    )
  })

  it('seeds the threshold from the persisted agentos_router.jev config', () => {
    const props = {
      catalog: catalogWithTiers(tiers),
      status: STATUS,
      config: {
        ...CONFIG,
        agentos_router: { enabled: true, strategy: 'jev', jev: { high_risk_threshold: 0.9 } },
      },
      onSave: vi.fn(),
      onBack: vi.fn(),
      onNext: vi.fn(),
      saving: false,
    }
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <RouterSection {...props} />
      </QueryClientProvider>,
    )
    expect(screen.getByLabelText('Router mode')).toHaveValue('jev')
    expect(screen.getByLabelText('High-risk floor threshold')).toHaveValue(0.9)
  })
})

// ── tier model pickers (#142) ───────────────────────────────────────────────

describe('RouterSection tier model pickers', () => {
  const PROFILE = {
    c0: { provider: 'openai', model: 'gpt-4o-mini' },
    c1: { provider: 'openai', model: 'gpt-4o' },
    image_model: { provider: 'openai', model: 'gpt-image-1', supports_image: true },
  }

  const LIVE = [
    {
      id: 'gpt-4o',
      name: 'GPT-4o',
      provider: 'openai',
      contextWindow: 128000,
      capabilities: ['chat', 'tools'],
      pricing: { inputPer1k: 0.0025, outputPer1k: 0.01 },
    },
    {
      id: 'gpt-image-1',
      name: 'GPT Image 1',
      provider: 'openai',
      contextWindow: 128000,
      capabilities: ['chat', 'vision'],
      pricing: { inputPer1k: 0.005, outputPer1k: 0.04 },
    },
  ]

  function optionsFor(tier: string): string[] {
    const list = document.getElementById(`setup-tier-models-${tier}`) as HTMLDataListElement | null
    return list ? Array.from(list.options).map((option) => option.value) : []
  }

  beforeEach(() => {
    modelsResponse = []
    vi.mocked(toast.warning).mockClear()
    mockRpc.call.mockClear()
  })

  it('asks the RPC to do the filtering, per provider and per capability', async () => {
    renderSection(catalogWithTiers(PROFILE))
    await waitFor(() => {
      const calls = mockRpc.call.mock.calls.filter(([method]) => method === 'models.list')
      expect(calls).toHaveLength(2)
      expect(calls.map(([, params]) => params)).toEqual(
        expect.arrayContaining([
          { provider: 'openai' },
          { provider: 'openai', capabilities: ['vision'] },
        ]),
      )
    })
  })

  it('offers the image tier only vision models, even with no live catalog', async () => {
    // A provider whose catalog the gateway never fetched — the common case
    // while the operator is still filling this form in.
    modelsResponse = []
    renderSection(catalogWithTiers(PROFILE))

    await waitFor(() => expect(optionsFor('c0')).toContain('gpt-4o'))
    expect(optionsFor('c0')).toEqual(['gpt-4o-mini', 'gpt-4o', 'gpt-image-1'])
    expect(optionsFor('image_model')).toEqual(['gpt-image-1'])
  })

  it('shows context window and price for the entered model', async () => {
    modelsResponse = LIVE
    renderSection(catalogWithTiers(PROFILE))
    expect(await screen.findByText('128k ctx · $2.50/$10.00 per 1M')).toBeInTheDocument()
  })

  it('saves an untouched form without warning about its own defaults', async () => {
    const onSave = vi.fn()
    renderSection(catalogWithTiers(PROFILE), onSave)
    await waitFor(() => expect(optionsFor('c0')).not.toHaveLength(0))

    fireEvent.click(screen.getByRole('button', { name: 'Save Router' }))
    expect(toast.warning).not.toHaveBeenCalled()
    expect(onSave).toHaveBeenCalled()
  })

  it('warns on an unknown id but still saves it', async () => {
    const onSave = vi.fn()
    renderSection(catalogWithTiers(PROFILE), onSave)
    await waitFor(() => expect(optionsFor('c0')).not.toHaveLength(0))

    fireEvent.change(screen.getByLabelText('c0 model'), {
      target: { value: 'z-ai/glm-5.2-turbo-max' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save Router' }))

    expect(toast.warning).toHaveBeenCalledWith(
      expect.stringContaining('z-ai/glm-5.2-turbo-max'),
      expect.any(Object),
    )
    expect(onSave).toHaveBeenCalled()
  })

  it('warns separately when the image tier is pointed at a text model', async () => {
    const onSave = vi.fn()
    modelsResponse = LIVE
    renderSection(catalogWithTiers(PROFILE), onSave)
    await waitFor(() => expect(optionsFor('c0')).toContain('gpt-4o'))

    fireEvent.change(screen.getByLabelText('image_model model'), { target: { value: 'gpt-4o' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save Router' }))

    expect(toast.warning).toHaveBeenCalledWith(
      expect.stringContaining('no vision capability'),
      expect.any(Object),
    )
    expect(toast.warning).not.toHaveBeenCalledWith(
      expect.stringContaining("openai's catalog"),
      expect.any(Object),
    )
    expect(onSave).toHaveBeenCalled()
  })

  it('says it could not check when no catalog exists at all', async () => {
    const onSave = vi.fn()
    const catalog = catalogWithTiers({ c0: { provider: 'openai' } })
    renderSection(catalog, onSave)

    fireEvent.change(screen.getByLabelText('c0 model'), { target: { value: 'some-model' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save Router' }))

    await waitFor(() =>
      expect(toast.warning).toHaveBeenCalledWith(
        expect.stringContaining('No model catalog available'),
        expect.any(Object),
      ),
    )
    expect(onSave).toHaveBeenCalled()
  })

  it('writes the configured provider on every tier, whatever the config held', async () => {
    const onSave = vi.fn()
    const catalog = catalogWithTiers({
      // A stale per-tier provider, reachable by the free-text cell this
      // replaces. The runtime ignores it (boot.py:1106-1120), so save
      // normalises rather than preserving it.
      c0: { provider: 'anthropic', model: 'gpt-4o-mini' },
      c1: { provider: 'openai', model: 'gpt-4o' },
    })
    renderSection(catalog, onSave)
    await waitFor(() => expect(optionsFor('c0')).not.toHaveLength(0))

    fireEvent.click(screen.getByRole('button', { name: 'Save Router' }))
    const params = onSave.mock.calls[0]![0] as { tiers: Record<string, { provider?: string }> }
    expect(Object.values(params.tiers).map((tier) => tier.provider)).toEqual(['openai', 'openai'])
  })

  it('tells the operator when there is nothing to pick from', async () => {
    renderSection(catalogWithTiers({ c0: { provider: 'openai' } }))
    expect(
      await screen.findByText('No catalog models known for openai — type an id.'),
    ).toBeInTheDocument()
  })
})
