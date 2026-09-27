import { toast } from 'sonner'
import { t } from '~/i18n'
import { useSetProvider } from '~/stores/trading'
import { errorText } from './logic'
import { providerLabel, type ProviderId } from './types'

/**
 * Switch the swap provider from anywhere on the desk, with the one toast
 * pair every caller wants. Settings is where a key is added; this is only
 * which route quotes the swaps.
 */
export function useSwitchProvider(): {
  switchTo: (id: ProviderId) => void
  switching: boolean
} {
  const setProvider = useSetProvider()
  return {
    switching: setProvider.isPending,
    switchTo: (id) =>
      setProvider.mutate(id, {
        onSuccess: (res) =>
          toast.success(`${t('trading.seat.provider.saved')}: ${providerLabel(res?.provider)}`, {
            id: 'trd-provider',
          }),
        onError: (err) => toast.error(errorText(err), { id: 'trd-provider-err' }),
      }),
  }
}
