import { t } from '~/i18n'
import { useNetwork } from '~/stores/trading'
import { chainShort } from './logic'
import type { NetworkChain } from './types'

/**
 * One dot per chain: green when the RPC's head is fresh, amber when it is
 * behind, red when it cannot be reached. The tooltip carries the numbers.
 * A balance read while a dot is not green is a balance from a node that
 * may be behind (see the engine's ``_native_after``), and that is the one
 * thing a person cannot tell from the balance itself.
 */
export function NetworkPips({ enabled = true }: { enabled?: boolean }) {
  const network = useNetwork(enabled)
  if (!network.data) return null
  return (
    <span className="trd-pips" role="group" aria-label={t('trading.network.title')}>
      {network.chains.map((c) => (
        <button
          key={c.chainId}
          type="button"
          className="trd-pip app-no-drag"
          data-state={pipState(c)}
          title={pipTitle(c)}
          aria-label={`${c.name}: ${pipWord(c)}`}
          onClick={() => void network.refetch()}
          data-testid={`pip-${c.chainId}`}
        >
          <span className="trd-pip__dot" aria-hidden />
          <span className="trd-pip__label">
            {chainShort(c.chainId)}
            {pipState(c) !== 'ok' ? ` · ${pipWord(c)}` : ''}
          </span>
        </button>
      ))}
    </span>
  )
}

export type PipState = 'ok' | 'stale' | 'down'

export function pipState(c: Pick<NetworkChain, 'healthy' | 'error' | 'blockNumber'>): PipState {
  if (c.error || c.blockNumber === null) return 'down'
  return c.healthy ? 'ok' : 'stale'
}

function pipWord(c: NetworkChain): string {
  const state = pipState(c)
  if (state === 'ok') return t('trading.network.healthy')
  if (state === 'stale') return t('trading.network.stale')
  return t('trading.network.down')
}

export function pipTitle(c: NetworkChain): string {
  const bits: string[] = [`${c.name} · ${pipWord(c)}`]
  if (c.blockNumber !== null) {
    bits.push(
      `${t('trading.network.head')} #${c.blockNumber}${
        c.blockAgeS !== null ? ` · ${t('trading.network.age')} ${c.blockAgeS}s` : ''
      }`,
    )
  }
  if (c.baseFeeGwei !== null) {
    bits.push(
      `${t('trading.network.gas')} ${trimGwei(c.baseFeeGwei)} gwei${
        c.priorityFeeGwei !== null
          ? ` · ${t('trading.network.tip')} ${trimGwei(c.priorityFeeGwei)} gwei`
          : ''
      }`,
    )
  }
  if (c.latencyMs !== null) bits.push(`${t('trading.network.latency')} ${c.latencyMs} ms`)
  if (c.error) bits.push(c.error)
  bits.push(c.rpcUrl)
  return bits.join('\n')
}

function trimGwei(value: number): string {
  if (value >= 1) return value.toFixed(2)
  if (value >= 0.001) return value.toFixed(4)
  return value.toPrecision(2)
}
