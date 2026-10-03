import { RouterProvider } from 'react-router'
import { ThemeProvider } from '~/theme/ThemeProvider'
import { isQuickAskWindow, QuickAskView } from '~/views/quick-ask/QuickAskView'
import { GatewayProviders } from './GatewayProviders'
import { router } from './routes'

export function App() {
  // The Quick Ask panel loads this same bundle at `#/quick-ask`. It is not a
  // route under the router: no AppShell, and no GatewayProviders either, so
  // the panel never opens a gateway connection of its own. Only the theme.
  if (isQuickAskWindow()) {
    return (
      <ThemeProvider>
        <QuickAskView />
      </ThemeProvider>
    )
  }
  return (
    <ThemeProvider>
      <GatewayProviders>
        <RouterProvider router={router} />
      </GatewayProviders>
    </ThemeProvider>
  )
}
