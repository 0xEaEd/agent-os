import { readFileSync } from 'node:fs'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it } from 'vitest'
import { TRADING_KEYS } from '~/stores/trading'
import type { MandateListPayload, MandatePayload } from '../types'
import { useTradeLedger } from './useTradeLedger'

const PAYLOAD = JSON.parse(
  readFileSync('src/renderer/src/views/trading/desk/__fixtures__/dca/mandate.json', 'utf8'),
) as MandatePayload

/** A thread with one rebuilt-from-history `trade dca create` block that recorded "awaiting approval". */
function thread(): HTMLElement {
  const root = document.createElement('div')
  const body = document.createElement('div')
  body.className = 'msg-body'
  const details = document.createElement('details')
  details.setAttribute('data-tool-name', 'exec_command')
  details.setAttribute('data-tool-id', 'toolu_1')
  const input = document.createElement('div')
  input.className = 'chat-tool-input'
  input.textContent = JSON.stringify({
    command: 'agentos trade dca create ETH --usd 10 --every 1d --cap 300 --json',
  })
  const preview = document.createElement('div')
  preview.className = 'chat-tool-result-preview'
  preview.textContent = `exit_code=0\n${JSON.stringify({
    ...PAYLOAD,
    mandate: { ...PAYLOAD.mandate, status: 'awaiting_approval' },
  })}`
  details.append(input, preview)
  body.appendChild(details)
  root.appendChild(body)
  document.body.appendChild(root)
  return root
}

function list(status: string): MandateListPayload {
  return {
    ...PAYLOAD,
    kind: 'mandates',
    mandates: [{ ...PAYLOAD.mandate, status: status as never }],
    totals: { count: 1, active: 0, spentUsd: 120, capUsd: 300, acquiredUsd: 0 },
  } as unknown as MandateListPayload
}

describe('a DCA ledger row against the live mandates', () => {
  it('keeps the recorded pill until the list knows the mandate, then follows it', async () => {
    const client = new QueryClient()
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    )
    const root = thread()
    const { result } = renderHook(() => useTradeLedger(() => {}, 'agent:trading:webchat:t'), {
      wrapper,
    })
    act(() => result.current.bind(root))
    const row = () => root.querySelector<HTMLElement>('.trd-ledger')!
    const stamp = () => row().querySelector<HTMLElement>('.trd-ledger__stamp')
    // No list loaded: as recorded.
    expect(row().dataset.state).toBe('awaiting')
    expect(stamp()).toHaveTextContent('Awaiting approval')

    // The mandate finished: the pill says so, and the awaiting stamp is gone.
    act(() => client.setQueryData(TRADING_KEYS.dca(true), list('completed')))
    await waitFor(() => expect(stamp()).toHaveTextContent('Done'))
    expect(stamp()!.dataset.tone).toBe('ok')
    expect(row().dataset.state).toBe('done')
    expect(row().querySelector('.trd-ledger__summary')?.textContent?.startsWith('done ·')).toBe(
      true,
    )

    act(() => client.setQueryData(TRADING_KEYS.dca(true), list('paused')))
    await waitFor(() => expect(stamp()).toHaveTextContent('Paused'))
    expect(stamp()!.dataset.tone).toBe('muted')

    act(() => client.setQueryData(TRADING_KEYS.dca(true), list('active')))
    await waitFor(() => expect(stamp()).toHaveTextContent('Active'))

    act(() => client.setQueryData(TRADING_KEYS.dca(true), list('stopped')))
    await waitFor(() => expect(stamp()).toHaveTextContent('Stopped'))
    root.remove()
  })
})
