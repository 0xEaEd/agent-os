import { readFileSync } from 'node:fs'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { renderDesk, WALLET } from '../test-utils'
import type { Mandate, MandatePayload } from '../types'
import { dcaCreateParams, type DcaForm } from './mandate-logic'
import { MissionContract } from './MissionContract'
import { presetById } from './presets'

const MANDATE = (
  JSON.parse(
    readFileSync('src/renderer/src/views/trading/desk/__fixtures__/dca/mandate.json', 'utf8'),
  ) as MandatePayload
).mandate

const limits = { dailyCapUsd: 1000, spentTodayUsd: 0, thresholdUsd: 100, approvalTtlSeconds: 900 }

/** The DCA handlers every contract takes; a cron test never reaches them. */
function mandateHandlers() {
  return { onCreateMandate: vi.fn(async () => ({})), onUpdateMandate: vi.fn(async () => ({})) }
}

describe('MissionContract', () => {
  it('prefills a dip mission, shows the prompt it composes, and creates the job', async () => {
    const onCreate = vi.fn(async () => ({}))
    const onClose = vi.fn()
    renderDesk(
      <MissionContract
        kind="dip"
        wallets={[WALLET]}
        primary={WALLET.address}
        limits={limits}
        onClose={onClose}
        onSend={vi.fn()}
        onCreate={onCreate}
        onUpdate={vi.fn(async () => {})}
        {...mandateHandlers()}
      />,
    )
    expect(screen.getByTestId('contract-name')).toHaveValue('Buy the dip')
    expect(screen.getByTestId('contract-interval')).toHaveValue('300')
    expect(screen.getByTestId('engine-limits')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('contract-preview-toggle'))
    const prompt = screen.getByTestId('contract-prompt').textContent ?? ''
    expect(prompt).toContain('[Trading desk mission] Buy the dip')
    expect(prompt).toContain('orders above $100.00 wait for approval')
    expect(prompt).toContain('Dry run')
    fireEvent.click(screen.getByTestId('contract-submit'))
    await waitFor(() => expect(onCreate).toHaveBeenCalledTimes(1))
    const [form, text] = onCreate.mock.calls[0] as unknown as [
      { kind: string; name: string },
      string,
    ]
    expect(form.kind).toBe('dip')
    expect(text).toBe(prompt)
    await waitFor(() => expect(onClose).toHaveBeenCalled())
  })

  it('stays open when the gateway refuses the job, so nothing typed is lost', async () => {
    const onCreate = vi.fn(async () => null)
    const onClose = vi.fn()
    renderDesk(
      <MissionContract
        kind="dip"
        wallets={[WALLET]}
        primary={WALLET.address}
        limits={limits}
        onClose={onClose}
        onSend={vi.fn()}
        onCreate={onCreate}
        onUpdate={vi.fn(async () => false)}
        {...mandateHandlers()}
      />,
    )
    fireEvent.change(screen.getByTestId('contract-name'), { target: { value: 'Mine' } })
    fireEvent.click(screen.getByTestId('contract-submit'))
    await waitFor(() => expect(onCreate).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(screen.getByTestId('contract-submit')).not.toBeDisabled())
    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByTestId('contract-name')).toHaveValue('Mine')
  })

  it('stays open when an edit is refused', async () => {
    const onUpdate = vi.fn(async () => false)
    const onClose = vi.fn()
    renderDesk(
      <MissionContract
        kind="custom"
        job={{
          id: 'j1',
          name: 'DCA ETH',
          message: 'Goal: buy',
          scheduleKind: 'every',
          scheduleRaw: 3600,
        }}
        wallets={[WALLET]}
        primary={WALLET.address}
        limits={limits}
        onClose={onClose}
        onSend={vi.fn()}
        onCreate={vi.fn(async () => ({}))}
        onUpdate={onUpdate}
        {...mandateHandlers()}
      />,
    )
    fireEvent.click(screen.getByTestId('contract-submit'))
    await waitFor(() => expect(onUpdate).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(screen.getByTestId('contract-submit')).not.toBeDisabled())
    expect(onClose).not.toHaveBeenCalled()
  })

  it('refuses an empty goal and names the missing field', () => {
    renderDesk(
      <MissionContract
        kind="custom"
        wallets={[WALLET]}
        primary={WALLET.address}
        limits={null}
        onClose={vi.fn()}
        onSend={vi.fn()}
        onCreate={vi.fn(async () => ({}))}
        onUpdate={vi.fn(async () => {})}
        {...mandateHandlers()}
      />,
    )
    // Pristine: the button is disabled but no copy scolds the user yet.
    expect(screen.getByTestId('contract-submit')).toBeDisabled()
    expect(screen.queryByText('Give the mission a name')).not.toBeInTheDocument()
    fireEvent.change(screen.getByTestId('contract-name'), { target: { value: 'Mine' } })
    expect(screen.getByTestId('contract-submit')).toBeDisabled()
    expect(screen.getByText('Say what the mission should do')).toBeInTheDocument()
  })

  it('opened from a preset, shows only its knobs until Advanced is asked for', () => {
    renderDesk(
      <MissionContract
        kind="custom"
        preset={presetById('dip')}
        wallets={[WALLET]}
        primary={WALLET.address}
        limits={limits}
        onBack={vi.fn()}
        onClose={vi.fn()}
        onSend={vi.fn()}
        onCreate={vi.fn(async () => ({}))}
        onUpdate={vi.fn(async () => {})}
        {...mandateHandlers()}
      />,
    )
    // The numbers that differ, plus the cadence. Nothing else.
    expect(screen.getByTestId('knob-token')).toHaveValue('ETH')
    expect(screen.getByTestId('knob-usd')).toHaveValue('25')
    expect(screen.getByTestId('contract-interval')).toHaveValue('300')
    expect(screen.queryByTestId('contract-goal')).toBeNull()
    expect(screen.queryByTestId('contract-budget')).toBeNull()

    fireEvent.click(screen.getByTestId('contract-advanced-toggle'))
    expect(screen.getByTestId('contract-goal')).toBeInTheDocument()
    expect(screen.getByTestId('contract-budget')).toHaveValue('100')
    // A cron mission's budget is prose to the agent, and the form says so.
    expect(screen.getByTestId('budget-note')).toHaveTextContent('Goals, not limits')
  })

  it('rewrites the goal as the knobs turn, and stops once the goal is written by hand', () => {
    renderDesk(
      <MissionContract
        kind="custom"
        preset={presetById('dip')}
        wallets={[WALLET]}
        primary={WALLET.address}
        limits={limits}
        onClose={vi.fn()}
        onSend={vi.fn()}
        onCreate={vi.fn(async () => ({}))}
        onUpdate={vi.fn(async () => {})}
        {...mandateHandlers()}
      />,
    )
    fireEvent.change(screen.getByTestId('knob-usd'), { target: { value: '75' } })
    fireEvent.click(screen.getByTestId('contract-advanced-toggle'))
    expect((screen.getByTestId('contract-goal') as HTMLTextAreaElement).value).toContain(
      'buy 75 USD of it',
    )
    expect(screen.getByTestId('contract-name')).toHaveValue('Buy ETH at 2300')

    // Hand-written wins: turning a knob afterwards must not throw it away.
    fireEvent.change(screen.getByTestId('contract-goal'), { target: { value: 'my own words' } })
    fireEvent.change(screen.getByTestId('knob-token'), { target: { value: 'WBTC' } })
    expect(screen.getByTestId('contract-goal')).toHaveValue('my own words')
  })

  it('warns on the preset that cannot promise what its name suggests', () => {
    renderDesk(
      <MissionContract
        kind="custom"
        preset={presetById('drawdown-alert')}
        wallets={[WALLET]}
        primary={WALLET.address}
        limits={limits}
        onClose={vi.fn()}
        onSend={vi.fn()}
        onCreate={vi.fn(async () => ({}))}
        onUpdate={vi.fn(async () => {})}
        {...mandateHandlers()}
      />,
    )
    expect(screen.getByTestId('preset-caveat')).toHaveTextContent('it does not sell')
  })

  it('sends a one-shot swap into the chat instead of scheduling it', async () => {
    const onSend = vi.fn()
    const onCreate = vi.fn(async () => ({}))
    renderDesk(
      <MissionContract
        kind="swap"
        wallets={[WALLET]}
        primary={WALLET.address}
        limits={limits}
        onClose={vi.fn()}
        onSend={onSend}
        onCreate={onCreate}
        onUpdate={vi.fn(async () => {})}
        {...mandateHandlers()}
      />,
    )
    expect(screen.queryByTestId('contract-interval')).toBeNull()
    expect(screen.getByTestId('contract-submit')).toHaveTextContent('Send')
    fireEvent.click(screen.getByTestId('contract-submit'))
    await waitFor(() => expect(onSend).toHaveBeenCalledTimes(1))
    expect(String(onSend.mock.calls[0]?.[0])).toContain('Goal: Swap 10 USDC to ETH on Base.')
    expect(onCreate).not.toHaveBeenCalled()
  })
})

describe('MissionContract · a DCA is a mandate the engine runs', () => {
  it('opens the mandate form from the DCA preset and sends numbers, not a prompt', async () => {
    const onCreate = vi.fn(async () => ({}))
    const onCreateMandate = vi.fn<(form: DcaForm) => Promise<unknown>>(async () => ({}))
    const onClose = vi.fn()
    renderDesk(
      <MissionContract
        kind="dca"
        preset={presetById('dca')}
        wallets={[WALLET]}
        primary={WALLET.address}
        limits={limits}
        onClose={onClose}
        onSend={vi.fn()}
        onCreate={onCreate}
        onUpdate={vi.fn(async () => {})}
        onCreateMandate={onCreateMandate}
        onUpdateMandate={vi.fn(async () => ({}))}
      />,
    )
    expect(screen.getByTestId('dca-contract')).toBeInTheDocument()
    // No cron prose, no "goals, not limits": the engine holds these.
    expect(screen.queryByTestId('contract-preview-toggle')).toBeNull()
    expect(screen.queryByTestId('budget-note')).toBeNull()
    expect(screen.getByTestId('dca-enforced')).toHaveTextContent('engine enforces the cap')
    expect(screen.getByTestId('dca-enforced')).toHaveTextContent('$300')
    expect(screen.getByTestId('dca-token')).toHaveValue('ETH')
    expect(screen.getByTestId('dca-usd')).toHaveValue('10')
    expect(screen.getByTestId('dca-every-1d')).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByTestId('dca-name')).toHaveValue('DCA ETH')

    fireEvent.change(screen.getByTestId('dca-token'), { target: { value: 'cbbtc' } })
    fireEvent.change(screen.getByTestId('dca-usd'), { target: { value: '25' } })
    fireEvent.click(screen.getByTestId('dca-every-6h'))
    fireEvent.change(screen.getByTestId('dca-runs'), { target: { value: '12' } })
    fireEvent.change(screen.getByTestId('dca-max-price'), { target: { value: '90000' } })
    fireEvent.click(screen.getByTestId('dca-start-now'))
    // The name follows the token until it is typed by hand.
    expect(screen.getByTestId('dca-name')).toHaveValue('DCA CBBTC under 90000')
    expect(screen.getByTestId('dca-summary')).toHaveTextContent(
      'Buys $25 of CBBTC every 6 hours until $300 is spent. At most 12 buys.',
    )
    fireEvent.click(screen.getByTestId('dca-submit'))
    await waitFor(() => expect(onCreateMandate).toHaveBeenCalledTimes(1))
    expect(onCreate).not.toHaveBeenCalled()
    await waitFor(() => expect(onClose).toHaveBeenCalled())

    const form = onCreateMandate.mock.calls[0]![0]
    expect(
      dcaCreateParams(form, { sessionKey: 'agent:trading:webchat:d', wallets: [WALLET] }),
    ).toEqual({
      chainId: 8453,
      token: 'CBBTC',
      usdPerRun: 25,
      everySeconds: 21_600,
      capUsd: 300,
      runsMax: 12,
      maxPriceUsd: 90000,
      wallet: WALLET.address,
      name: 'DCA CBBTC under 90000',
      startNow: false,
      sessionKey: 'agent:trading:webchat:d',
    })
  })

  it('prefills the price ceiling from the capped preset and warns when every buy will park', () => {
    renderDesk(
      <MissionContract
        kind="custom"
        preset={presetById('dca-capped')}
        wallets={[WALLET]}
        primary={WALLET.address}
        limits={limits}
        onClose={vi.fn()}
        onSend={vi.fn()}
        onCreate={vi.fn(async () => ({}))}
        onUpdate={vi.fn(async () => {})}
        {...mandateHandlers()}
      />,
    )
    expect(screen.getByTestId('dca-max-price')).toHaveValue('3000')
    expect(screen.queryByTestId('dca-over-threshold')).toBeNull()
    fireEvent.change(screen.getByTestId('dca-usd'), { target: { value: '150' } })
    expect(screen.getByTestId('dca-over-threshold')).toHaveTextContent(
      'Each buy of $150 is above the $100 approval threshold',
    )
  })

  it('names the missing field and refuses a cadence under an hour or a cap below one buy', () => {
    renderDesk(
      <MissionContract
        kind="dca"
        wallets={[WALLET]}
        primary={WALLET.address}
        limits={limits}
        onClose={vi.fn()}
        onSend={vi.fn()}
        onCreate={vi.fn(async () => ({}))}
        onUpdate={vi.fn(async () => {})}
        {...mandateHandlers()}
      />,
    )
    fireEvent.click(screen.getByTestId('dca-every-custom'))
    fireEvent.change(screen.getByTestId('dca-every-input'), { target: { value: '30m' } })
    expect(screen.getByTestId('dca-submit')).toBeDisabled()
    expect(screen.getByTestId('dca-error')).toHaveTextContent('an hour or more')
    fireEvent.change(screen.getByTestId('dca-every-input'), { target: { value: '2d' } })
    fireEvent.change(screen.getByTestId('dca-cap'), { target: { value: '5' } })
    expect(screen.getByTestId('dca-error')).toHaveTextContent('at least one buy')
    fireEvent.change(screen.getByTestId('dca-cap'), { target: { value: '' } })
    expect(screen.getByTestId('dca-error')).toHaveTextContent('a number of buys')
    fireEvent.change(screen.getByTestId('dca-runs'), { target: { value: '8' } })
    expect(screen.getByTestId('dca-submit')).not.toBeDisabled()
    // Robinhood Chain has no canonical USDC: the token paid with is required.
    fireEvent.click(screen.getByTestId('dca-chain-robinhood'))
    expect(screen.getByTestId('dca-error')).toHaveTextContent('name the token you pay with')
  })

  it('edits a mandate with trading.dca.update fields only, the token and wallet fixed', async () => {
    const onUpdateMandate = vi.fn<(m: Mandate, patch: Record<string, unknown>) => Promise<unknown>>(
      async () => ({}),
    )
    renderDesk(
      <MissionContract
        kind="dca"
        mandate={MANDATE}
        wallets={[WALLET]}
        primary={WALLET.address}
        limits={limits}
        onClose={vi.fn()}
        onSend={vi.fn()}
        onCreate={vi.fn(async () => ({}))}
        onUpdate={vi.fn(async () => {})}
        onCreateMandate={vi.fn(async () => ({}))}
        onUpdateMandate={onUpdateMandate}
      />,
    )
    expect(screen.getByTestId('dca-token')).toBeDisabled()
    expect(screen.getByTestId('dca-wallet')).toBeDisabled()
    expect(screen.queryByTestId('dca-start-now')).toBeNull()
    // Nothing changed yet: nothing to save.
    expect(screen.getByTestId('dca-submit')).toBeDisabled()
    fireEvent.change(screen.getByTestId('dca-cap'), { target: { value: '500' } })
    fireEvent.click(screen.getByTestId('dca-every-12h'))
    fireEvent.change(screen.getByTestId('dca-max-price'), { target: { value: '' } })
    fireEvent.click(screen.getByTestId('dca-submit'))
    await waitFor(() => expect(onUpdateMandate).toHaveBeenCalledTimes(1))
    const [m, patch] = onUpdateMandate.mock.calls[0]!
    expect(m.id).toBe('dca_1a2b3c4d')
    // A cleared ceiling is a 0: the engine drops the price guard.
    expect(patch).toEqual({ capUsd: 500, everySeconds: 43_200, maxPriceUsd: 0 })
  })

  it('keeps editing an old cron-based DCA job in the cron contract', () => {
    renderDesk(
      <MissionContract
        kind="custom"
        job={{
          id: 'j1',
          name: 'DCA ETH',
          message: 'Goal: buy',
          scheduleKind: 'every',
          scheduleRaw: 86400,
        }}
        wallets={[WALLET]}
        primary={WALLET.address}
        limits={limits}
        onClose={vi.fn()}
        onSend={vi.fn()}
        onCreate={vi.fn(async () => ({}))}
        onUpdate={vi.fn(async () => {})}
        {...mandateHandlers()}
      />,
    )
    expect(screen.getByTestId('mission-contract')).toBeInTheDocument()
    expect(screen.queryByTestId('dca-contract')).toBeNull()
  })
})
