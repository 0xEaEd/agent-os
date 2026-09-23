import { fireEvent, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { renderDesk, WALLET } from '../test-utils'
import { MissionContract } from './MissionContract'
import { presetById } from './presets'

const limits = { dailyCapUsd: 1000, spentTodayUsd: 0, thresholdUsd: 100, approvalTtlSeconds: 900 }

describe('MissionContract', () => {
  it('prefills a DCA mission, shows the prompt it composes, and creates the job', async () => {
    const onCreate = vi.fn(async () => ({}))
    const onClose = vi.fn()
    renderDesk(
      <MissionContract
        kind="dca"
        wallets={[WALLET]}
        primary={WALLET.address}
        limits={limits}
        onClose={onClose}
        onSend={vi.fn()}
        onCreate={onCreate}
        onUpdate={vi.fn(async () => {})}
      />,
    )
    expect(screen.getByTestId('contract-name')).toHaveValue('DCA ETH')
    expect(screen.getByTestId('contract-interval')).toHaveValue('86400')
    expect(screen.getByTestId('engine-limits')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('contract-preview-toggle'))
    const prompt = screen.getByTestId('contract-prompt').textContent ?? ''
    expect(prompt).toContain('[Trading desk mission] DCA ETH')
    expect(prompt).toContain('orders above $100.00 wait for approval')
    expect(prompt).toContain('Dry run')
    fireEvent.click(screen.getByTestId('contract-submit'))
    await waitFor(() => expect(onCreate).toHaveBeenCalledTimes(1))
    const [form, text] = onCreate.mock.calls[0] as unknown as [
      { kind: string; name: string },
      string,
    ]
    expect(form.kind).toBe('dca')
    expect(text).toBe(prompt)
    await waitFor(() => expect(onClose).toHaveBeenCalled())
  })

  it('stays open when the gateway refuses the job, so nothing typed is lost', async () => {
    const onCreate = vi.fn(async () => null)
    const onClose = vi.fn()
    renderDesk(
      <MissionContract
        kind="dca"
        wallets={[WALLET]}
        primary={WALLET.address}
        limits={limits}
        onClose={onClose}
        onSend={vi.fn()}
        onCreate={onCreate}
        onUpdate={vi.fn(async () => false)}
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
        preset={presetById('dca')}
        wallets={[WALLET]}
        primary={WALLET.address}
        limits={limits}
        onBack={vi.fn()}
        onClose={vi.fn()}
        onSend={vi.fn()}
        onCreate={vi.fn(async () => ({}))}
        onUpdate={vi.fn(async () => {})}
      />,
    )
    // The two numbers that differ, plus the cadence. Nothing else.
    expect(screen.getByTestId('knob-token')).toHaveValue('ETH')
    expect(screen.getByTestId('knob-usd')).toHaveValue('10')
    expect(screen.getByTestId('contract-interval')).toHaveValue('86400')
    expect(screen.queryByTestId('contract-goal')).toBeNull()
    expect(screen.queryByTestId('contract-budget')).toBeNull()

    fireEvent.click(screen.getByTestId('contract-advanced-toggle'))
    expect(screen.getByTestId('contract-goal')).toBeInTheDocument()
    expect(screen.getByTestId('contract-budget')).toHaveValue('300')
  })

  it('rewrites the goal as the knobs turn, and stops once the goal is written by hand', () => {
    renderDesk(
      <MissionContract
        kind="custom"
        preset={presetById('dca')}
        wallets={[WALLET]}
        primary={WALLET.address}
        limits={limits}
        onClose={vi.fn()}
        onSend={vi.fn()}
        onCreate={vi.fn(async () => ({}))}
        onUpdate={vi.fn(async () => {})}
      />,
    )
    fireEvent.change(screen.getByTestId('knob-usd'), { target: { value: '75' } })
    fireEvent.click(screen.getByTestId('contract-advanced-toggle'))
    expect((screen.getByTestId('contract-goal') as HTMLTextAreaElement).value).toContain(
      '75 USD of ETH',
    )
    expect(screen.getByTestId('contract-name')).toHaveValue('DCA ETH')

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
