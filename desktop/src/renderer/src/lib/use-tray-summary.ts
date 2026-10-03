import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useRpc } from '@/app/providers'
import { useApprovals } from '@/services/approval-monitor'
import { useConnection } from '@/stores/connection'
import { desktopApi, isDesktop } from '~/lib/desktop-api'
import { useLive } from '~/stores/live'
import { TRADING_EVENTS, TRADING_KEYS } from '~/stores/trading'
import { readTradingSessionKey } from '~/stores/trading-ui'
import type { Mandate, MandateListPayload, Order } from '~/views/trading/types'
import { createSummaryThrottle, traySummaryFrom, type SummaryThrottle } from './tray-summary'

/** The desk's own "awaiting" page: same key and params as `usePendingApprovals`. */
const AWAITING_LIMIT = 20
const AWAITING_KEY = TRADING_KEYS.orders('awaiting_approval', AWAITING_LIMIT)
const LIVE_MANDATES_KEY = TRADING_KEYS.dca(false)
/** Events drive these; this only catches one that was missed. */
const BACKSTOP_MS = 60_000

const NO_ORDERS: Order[] = []
const NO_MANDATES: Mandate[] = []

interface OrderList {
  orders?: Order[]
  pendingApprovals?: number
}

/**
 * Keeps the menu bar item current: live turns, approvals waiting (tool
 * approvals and desk orders), the next DCA buy. Bound once from the shell;
 * pushes at most once a second, and not at all in a browser tab.
 */
export function useTraySummary(): void {
  const desktop = isDesktop()
  const liveTurns = useLive((s) => s.ids.size)
  const toolApprovals = useApprovals((s) => s.pending.length)
  const desk = useDeskWaiting(desktop)

  const summary = useMemo(
    () =>
      traySummaryFrom({
        liveTurns,
        toolApprovals,
        orders: desk.orders,
        ordersPending: desk.ordersPending,
        mandates: desk.mandates,
      }),
    [liveTurns, toolApprovals, desk.orders, desk.ordersPending, desk.mandates],
  )

  const throttle = useRef<SummaryThrottle | null>(null)
  useEffect(() => {
    if (!desktop) return
    const t = createSummaryThrottle((s) => desktopApi().tray.setSummary(s))
    throttle.current = t
    return () => {
      t.cancel()
      throttle.current = null
    }
  }, [desktop])
  useEffect(() => {
    throttle.current?.push(summary)
  }, [summary])
}

/**
 * What waits at the desk, whether or not the desk is on screen: the orders
 * awaiting approval and the live mandates. These are the desk's own queries
 * (same keys, so one cache), but event-driven with a slow backstop instead
 * of the desk's polling.
 *
 * Asking the engine for them starts its trading loop, which an ordinary
 * chat must not do (`useTradingInvalidation`). So they only run once
 * trading is in use: a desk session exists on this Mac, or a trading event
 * arrived (an agent placed an order or a DCA from a chat).
 */
function useDeskWaiting(enabled: boolean): {
  orders: readonly Order[]
  ordersPending: number
  mandates: readonly Mandate[]
} {
  const rpc = useRpc()
  const queryClient = useQueryClient()
  const connected = useConnection((s) => s.state === 'connected')
  const [sawTrading, setSawTrading] = useState(false)
  const inUse = sawTrading || readTradingSessionKey() !== ''
  const live = enabled && connected && inUse

  useEffect(() => {
    if (!enabled || !connected) return
    let timer: ReturnType<typeof setTimeout> | null = null
    const refresh = () => {
      if (timer) return
      timer = setTimeout(() => {
        timer = null
        // Not cancelRefetch: when the desk's own invalidation already has
        // these in flight, ride along instead of restarting them.
        for (const queryKey of [AWAITING_KEY, LIVE_MANDATES_KEY]) {
          void queryClient.invalidateQueries({ queryKey, exact: true }, { cancelRefetch: false })
        }
      }, 150)
    }
    const offs = TRADING_EVENTS.map((event) =>
      rpc.on(event, () => {
        // `_hello` is a reconnect, not trading activity.
        if (event !== '_hello') setSawTrading(true)
        refresh()
      }),
    )
    return () => {
      offs.forEach((off) => off())
      if (timer) clearTimeout(timer)
    }
  }, [rpc, queryClient, enabled, connected])

  const orders = useQuery<OrderList>({
    queryKey: AWAITING_KEY,
    enabled: live,
    queryFn: async () => {
      await rpc.waitForConnection()
      return rpc.call<OrderList>('trading.orders.list', {
        status: 'awaiting_approval',
        limit: AWAITING_LIMIT,
      })
    },
    refetchInterval: BACKSTOP_MS,
    placeholderData: (prev) => prev,
  })
  const mandates = useQuery<MandateListPayload>({
    queryKey: LIVE_MANDATES_KEY,
    enabled: live,
    queryFn: async () => {
      await rpc.waitForConnection()
      return rpc.call<MandateListPayload>('trading.dca.list', {})
    },
    refetchInterval: BACKSTOP_MS,
    placeholderData: (prev) => prev,
  })

  // A disconnected gateway has nothing waiting that the user could act on.
  const orderList = live ? (orders.data?.orders ?? NO_ORDERS) : NO_ORDERS
  const mandateList =
    live && Array.isArray(mandates.data?.mandates) ? mandates.data.mandates : NO_MANDATES
  return {
    orders: orderList,
    ordersPending: live ? (orders.data?.pendingApprovals ?? 0) : 0,
    mandates: mandateList,
  }
}
