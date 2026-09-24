import { useQuery } from '@tanstack/react-query'
import { useRpc } from '@/app/providers'
import { useConnection } from '@/stores/connection'

export interface GatewayStatusResult {
  version?: string
  uptime_ms?: number
  provider?: string | null
  active_sessions?: number
}

/**
 * The gateway's `status` RPC, shared by About and the shell's update notice
 * under one query key so both read the same answer. Polled slowly; the
 * version is what tells a freshly installed engine from the one still
 * running, and the session count is what an update must not interrupt
 * unasked.
 */
export function useGatewayStatus() {
  const rpc = useRpc()
  const connected = useConnection((s) => s.state === 'connected')
  return useQuery({
    queryKey: ['settings', 'status'],
    enabled: connected,
    refetchInterval: 30_000,
    queryFn: () => rpc.call<GatewayStatusResult>('status'),
  })
}
