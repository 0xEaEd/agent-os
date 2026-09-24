import { Navigate, useLocation } from 'react-router'
import { ensureTradingSessionKey } from '~/stores/trading-ui'
import { ENTER_STATE, mintTradingSessionKey, redirectTarget } from './mode-logic'

/**
 * `/trading` is not a page any more: it is the desk's session. Notifications
 * and deep links (`?order=`, `?desk=1`) still arrive here and are forwarded
 * with their query, marked as a request for the desk so the entrance plays.
 */
export function TradingRedirect() {
  const { search } = useLocation()
  const key = ensureTradingSessionKey(mintTradingSessionKey)
  return <Navigate to={redirectTarget(key, search)} replace state={ENTER_STATE} />
}
