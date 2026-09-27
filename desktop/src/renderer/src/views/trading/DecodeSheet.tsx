import { ExternalLink, Search } from 'lucide-react'
import { useState } from 'react'
import { Button } from '~/components/ui/button'
import { t } from '~/i18n'
import { desktopApi } from '~/lib/desktop-api'
import { useDecode } from '~/stores/trading'
import { ChainBadge } from './ChainMark'
import { errorText, formatAmount, fromRaw, shortAddress } from './logic'
import { AssetCell, Sheet, Spinner } from './parts'
import { nativeToken } from './TokenPicker'
import { CHAINS, type Decoded } from './types'

const HASH_RE = /^0x[0-9a-fA-F]{64}$/
const HEX_RE = /^0x[0-9a-fA-F]*$/

/**
 * A transaction hash or raw calldata, explained by the engine: the function
 * (named only when it is one the desk knows; a selector otherwise), the
 * receipt's transfers and approvals with token metadata, and whether one of
 * the vault's wallets took part.
 */
export function DecodeSheet({
  chainId: initialChain,
  hash: initialHash = '',
  onClose,
}: {
  chainId: number
  hash?: string
  onClose: () => void
}) {
  const [chainId, setChainId] = useState(initialChain)
  const [text, setText] = useState(initialHash)
  const [to, setTo] = useState('')
  const decode = useDecode()
  const trimmed = text.trim()
  const isHash = HASH_RE.test(trimmed)
  const isData = !isHash && HEX_RE.test(trimmed) && trimmed.length >= 10
  const can = isHash || isData

  function run() {
    if (!can) return
    decode.mutate(
      isHash
        ? { chainId, txHash: trimmed }
        : { chainId, data: trimmed, ...(to.trim() ? { to: to.trim() } : {}) },
    )
  }

  const result = decode.data ?? null
  return (
    <Sheet
      title={t('trading.decode.title')}
      onClose={onClose}
      wide
      foot={
        <>
          <Button variant="ghost" onClick={onClose}>
            {t('trading.sheet.close')}
          </Button>
          <Button
            variant="primary"
            disabled={!can || decode.isPending}
            onClick={run}
            data-testid="decode-run"
          >
            {decode.isPending ? <Spinner className="size-3.5" /> : <Search className="size-3.5" />}
            {decode.isPending ? t('trading.decode.decoding') : t('trading.decode.cta')}
          </Button>
        </>
      }
    >
      <div className="trd-decode" data-testid="decode-sheet">
        <div className="trd-send__row">
          <label className="trd-send__field">
            <span>{t('trading.send.chain')}</span>
            <select
              className="mac-input"
              value={chainId}
              onChange={(e) => setChainId(Number(e.target.value))}
              data-testid="decode-chain"
            >
              {CHAINS.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>
          <label className="trd-send__field trd-send__field--wide">
            <span>{t('trading.decode.hash')}</span>
            <input
              className="mac-input trd-mono"
              value={text}
              placeholder={t('trading.decode.hash.placeholder')}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') run()
              }}
              spellCheck={false}
              data-testid="decode-input"
            />
          </label>
          {isData ? (
            <label className="trd-send__field">
              <span>{t('trading.decode.to')}</span>
              <input
                className="mac-input trd-mono"
                value={to}
                placeholder={t('trading.decode.hash.placeholder')}
                onChange={(e) => setTo(e.target.value)}
                spellCheck={false}
                data-testid="decode-to"
              />
            </label>
          ) : null}
        </div>
        {decode.isError ? (
          <p className="trd-send__error" role="alert">
            {t('trading.decode.failed')}: {errorText(decode.error)}
          </p>
        ) : null}
        {result ? (
          <Result result={result} />
        ) : !decode.isPending ? (
          <p className="trd-decode__empty">{t('trading.decode.empty')}</p>
        ) : null}
      </div>
    </Sheet>
  )
}

function Result({ result }: { result: Decoded }) {
  const fn = result.call.function
  const tx = result.tx
  return (
    <div className="trd-decode__result" data-testid="decode-result">
      <p className="trd-decode__desc">{result.description}</p>
      <dl className="trd-card__facts">
        <Fact label={t('trading.decode.function')}>
          {result.call.selector === '0x' ? (
            t('trading.decode.plain')
          ) : fn ? (
            <>
              {fn}
              {!result.call.known ? <em> · {t('trading.decode.unknown')}</em> : null}
            </>
          ) : (
            <>
              {result.call.selector} <em>· {t('trading.decode.unknown')}</em>
            </>
          )}
        </Fact>
        {result.to ? (
          <Fact label={t('trading.decode.to.label')}>
            {result.toLabel ?? result.toToken?.symbol ?? ''} {shortAddress(result.to)}
          </Fact>
        ) : null}
        {tx ? (
          <>
            <Fact label={t('trading.decode.status')}>
              <span data-tone={tx.status === 'reverted' ? 'danger' : undefined}>
                {t(`trading.decode.status.${tx.status}`)}
              </span>
            </Fact>
            {tx.from ? <Fact label={t('trading.decode.from')}>{shortAddress(tx.from)}</Fact> : null}
            {tx.valueWei !== '0' ? (
              <Fact label={t('trading.decode.value')}>
                {formatAmount(fromRaw(tx.valueWei, 18))} {nativeToken(result.chainId).symbol}
              </Fact>
            ) : null}
            {tx.gasUsed !== null ? (
              <Fact label={t('trading.decode.gas')}>{tx.gasUsed.toLocaleString()}</Fact>
            ) : null}
          </>
        ) : null}
      </dl>
      {result.decoded ? (
        <div className="trd-decode__legs" data-testid="decode-decoded">
          <AssetCell token={result.decoded.token} />
          <b className="trd-num">
            {result.decoded.unlimited
              ? t('trading.allowances.unlimited')
              : `${formatAmount(result.decoded.amount)} ${result.decoded.token.symbol}`}
          </b>
          <span aria-hidden>→</span>
          <span className="trd-mono" title={result.decoded.counterparty}>
            {result.decoded.counterpartyLabel ?? shortAddress(result.decoded.counterparty)}
          </span>
        </div>
      ) : null}
      {result.transfers.length ? (
        <section className="trd-decode__moves">
          <h4>{t('trading.decode.transfers')}</h4>
          <ul>
            {result.transfers.map((m) => (
              <li key={`${m.logIndex}`} className="trd-decode__move">
                <AssetCell token={m.token} />
                <b className="trd-num">
                  {formatAmount(m.amount)} {m.token.symbol}
                </b>
                <span className="trd-mono">
                  {shortAddress(m.from)} → {shortAddress(m.to)}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {result.approvals.length ? (
        <section className="trd-decode__moves">
          <h4>{t('trading.decode.approvals')}</h4>
          <ul>
            {result.approvals.map((g) => (
              <li key={`${g.logIndex}`} className="trd-decode__move">
                <AssetCell token={g.token} />
                <b className="trd-num" data-tone={g.unlimited ? 'danger' : undefined}>
                  {g.unlimited ? t('trading.allowances.unlimited') : formatAmount(g.amount)}
                </b>
                <span className="trd-mono">
                  {shortAddress(g.owner)} → {g.spenderLabel ?? shortAddress(g.spender)}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      <footer className="trd-decode__foot">
        <ChainBadge chainId={result.chainId} />
        {result.wallets.length ? (
          <span data-testid="decode-yours">
            {t('trading.decode.yours')}: {result.wallets.map((w) => shortAddress(w)).join(', ')}
          </span>
        ) : null}
        {result.explorerUrl ? (
          <button
            type="button"
            className="trd-card__link app-no-drag"
            onClick={() => void desktopApi().app.openExternal(result.explorerUrl as string)}
          >
            {t('trading.history.explorer')}
            <ExternalLink className="size-3" strokeWidth={1.75} aria-hidden />
          </button>
        ) : null}
      </footer>
    </div>
  )
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="trd-card__fact">
      <dt>{label}</dt>
      <dd className="trd-mono">{children}</dd>
    </div>
  )
}
