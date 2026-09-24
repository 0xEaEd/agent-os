import { Copy, ExternalLink, KeyRound, Pencil, QrCode, Star, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { MenuItem } from '~/components/menu/PopMenu'
import { t } from '~/i18n'
import { desktopApi } from '~/lib/desktop-api'
import type { ChainStatus, Wallet } from './types'
import type { WalletAction } from './WalletRail'

/**
 * Everything you can do to one wallet, as menu rows.
 *
 * Two places open this menu — the rail's row and the head's ⋯ — and they must
 * offer the same things in the same order, or the desk has two answers to
 * "where do I rename a wallet". One list, two triggers.
 */
export function WalletMenuItems({
  wallet,
  chains,
  onAction,
  onSetPrimary,
}: {
  wallet: Wallet
  chains: ChainStatus[]
  onAction: (action: WalletAction) => void
  onSetPrimary: () => void
}) {
  return (
    <>
      <MenuItem
        icon={Copy}
        label={t('trading.rail.receive')}
        onSelect={() => void copyAddress(wallet.address)}
      />
      <MenuItem
        icon={QrCode}
        label={t('trading.rail.qr')}
        onSelect={() => onAction({ kind: 'receive', wallet })}
      />
      {chains.map((c) => (
        <MenuItem
          key={c.chainId}
          icon={ExternalLink}
          label={`${t('trading.rail.explorer')} · ${c.name}`}
          onSelect={() =>
            void desktopApi().app.openExternal(`${c.explorer}/address/${wallet.address}`)
          }
        />
      ))}
      {!wallet.primary ? (
        <MenuItem icon={Star} label={t('trading.rail.setPrimary')} onSelect={onSetPrimary} />
      ) : null}
      <MenuItem
        icon={Pencil}
        label={t('trading.rail.rename')}
        onSelect={() => onAction({ kind: 'rename', wallet })}
      />
      <MenuItem
        icon={KeyRound}
        label={t('trading.rail.export')}
        onSelect={() => onAction({ kind: 'export', wallet })}
      />
      <MenuItem
        icon={Trash2}
        tone="danger"
        label={t('trading.rail.remove')}
        onSelect={() => onAction({ kind: 'remove', wallet })}
      />
    </>
  )
}

/** Put an address on the clipboard and say so. Silent where there is no clipboard. */
export async function copyAddress(address: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(address)
    toast.success(t('trading.rail.copied'), { id: 'trd-copy' })
  } catch {
    /* clipboard unavailable */
  }
}
