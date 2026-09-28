import { readFileSync } from 'node:fs'
import { describe, expect, it, vi } from 'vitest'
import { parseTradeCommand, parseTradeResult, txExplorerUrl } from './ledger'
import { txHashNode } from './useTradeLedger'

const FIXTURES = 'src/renderer/src/views/trading/desk/__fixtures__/lp'
const TX = `0x${'a9'.repeat(32)}`
const BASESCAN = 'https://basescan.org'
const explorers = (chainId: number): string | null => (chainId === 8453 ? BASESCAN : null)

function order(kind: string): Record<string, unknown> {
  return JSON.parse(readFileSync(`${FIXTURES}/${kind}.json`, 'utf8')) as Record<string, unknown>
}

describe('txExplorerUrl', () => {
  it("prefers the order's own link", () => {
    const own = `https://example.org/tx/${TX}`
    expect(txExplorerUrl({ txHash: TX, explorerUrl: own, chainId: 8453 }, explorers)).toBe(own)
  })

  it("falls back to the chain's explorer + /tx/<hash>", () => {
    expect(txExplorerUrl({ txHash: TX, explorerUrl: null, chainId: 8453 }, explorers)).toBe(
      `${BASESCAN}/tx/${TX}`,
    )
    // A trailing slash on the base is not doubled.
    expect(
      txExplorerUrl({ txHash: TX, explorerUrl: null, chainId: 8453 }, () => `${BASESCAN}/`),
    ).toBe(`${BASESCAN}/tx/${TX}`)
  })

  it('is null without a hash, a chain it knows, or a web link', () => {
    expect(txExplorerUrl({ txHash: null, explorerUrl: null, chainId: 8453 }, explorers)).toBeNull()
    expect(txExplorerUrl({ txHash: TX, explorerUrl: null, chainId: 4663 }, explorers)).toBeNull()
    expect(txExplorerUrl({ txHash: TX, explorerUrl: null, chainId: null }, explorers)).toBeNull()
    expect(
      txExplorerUrl({ txHash: TX, explorerUrl: 'javascript:alert(1)', chainId: null }, explorers),
    ).toBeNull()
    expect(
      txExplorerUrl({ txHash: TX, explorerUrl: null, chainId: 8453 }, () => 'file:///etc'),
    ).toBeNull()
    // A short (truncated or fake) hash never becomes a URL of its own.
    expect(
      txExplorerUrl({ txHash: '0xabc', explorerUrl: null, chainId: 8453 }, explorers),
    ).toBeNull()
  })
})

describe('the chain a ledger row can link through', () => {
  it('is read off a parsed order: an LP write, a swap and a send alike', () => {
    const lp = parseTradeResult(
      parseTradeCommand('agentos trade lp remove 48213 --chain base --wait --json')!,
      JSON.stringify({ ...order('lp_remove'), status: 'confirmed', txHash: TX }),
    )
    expect(lp.chainId).toBe(8453)
    expect(txExplorerUrl(lp, explorers)).toBe(`${BASESCAN}/tx/${TX}`)

    for (const cmd of [
      'agentos trade swap --chain base --in ETH --out USDC --amount 1 --json',
      'agentos trade send --chain base --token USDC --to 0x1 --amount 1 --json',
    ]) {
      const out = parseTradeResult(
        parseTradeCommand(cmd)!,
        JSON.stringify({
          orders: [{ orderId: 'o1', chainId: 8453, status: 'confirmed', txHash: TX }],
        }),
      )
      expect(out.chainId).toBe(8453)
      expect(txExplorerUrl(out, explorers)).toBe(`${BASESCAN}/tx/${TX}`)
    }
  })

  it('is read off a truncated LP result too', () => {
    const text = `exit_code=0\n${JSON.stringify({
      order: { ...order('lp_remove'), status: 'confirmed', txHash: TX, explorerUrl: null },
    })}`.slice(0, 2_000)
    const out = parseTradeResult(
      parseTradeCommand('agentos trade lp remove 48213 --chain base --wait --json')!,
      text,
    )
    expect(out.txHash).toBe(TX)
    expect(out.chainId).toBe(8453)
    expect(txExplorerUrl(out, explorers)).toBe(`${BASESCAN}/tx/${TX}`)
  })
})

describe('txHashNode', () => {
  it('is a link that opens a new tab, without an opener', () => {
    const node = txHashNode(TX, `${BASESCAN}/tx/${TX}`) as HTMLAnchorElement
    expect(node.tagName).toBe('A')
    expect(node.href).toBe(`${BASESCAN}/tx/${TX}`)
    expect(node.target).toBe('_blank')
    expect(node.rel.split(' ')).toContain('noopener')
    expect(node.dataset.tx).toBe('link')
    expect(node.textContent).toBe(`${TX.slice(0, 8)}…`)
    expect(node.title).toContain(TX)
  })

  it('without an explorer page it copies the hash instead of doing nothing', async () => {
    const writeText = vi.fn(() => Promise.resolve())
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    const node = txHashNode(TX, null)
    expect(node.tagName).toBe('BUTTON')
    expect(node.dataset.tx).toBe('copy')
    node.click()
    await Promise.resolve()
    await Promise.resolve()
    expect(writeText).toHaveBeenCalledWith(TX)
    expect(node.textContent).toBe('copied')
  })
})
