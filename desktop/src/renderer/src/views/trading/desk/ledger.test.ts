import { describe, expect, it } from 'vitest'
import {
  commandFromToolInput,
  exitCodeOf,
  ledgerRuns,
  parseTradeCommand,
  parseTradeResult,
} from './ledger'

describe('commandFromToolInput', () => {
  it('reads the command out of an object, a JSON string, or a truncated preview', () => {
    expect(commandFromToolInput({ command: 'agentos trade status --json' })).toBe(
      'agentos trade status --json',
    )
    expect(commandFromToolInput('{"command": "agentos wallet balances --json"}')).toBe(
      'agentos wallet balances --json',
    )
    expect(commandFromToolInput('{\n  "command": "agentos trade quote --chain base --in ETH')).toBe(
      'agentos trade quote --chain base --in ETH',
    )
    expect(commandFromToolInput('ls -la')).toBe('ls -la')
    expect(commandFromToolInput(null)).toBeNull()
  })
})

describe('parseTradeCommand', () => {
  it('recognises desk commands and summarises their arguments', () => {
    const swap = parseTradeCommand(
      'uv run agentos trade swap --chain base --in ETH --out USDC --amount 0.01 --wait --json',
    )
    expect(swap).toMatchObject({ kind: 'swap', title: 'Swap', detail: '0.01 ETH → USDC · Base' })
    expect(
      parseTradeCommand('agentos trade quote --chain robinhood --in USDC --out AAPL --pct 50'),
    ).toMatchObject({ kind: 'quote', detail: '50% USDC → AAPL · Robinhood' })
    expect(parseTradeCommand('agentos wallet balances --json')).toMatchObject({ kind: 'balances' })
    expect(parseTradeCommand('agentos wallet list --json')).toMatchObject({
      kind: 'wallet',
      detail: 'list',
    })
    expect(
      parseTradeCommand('agentos trade order ord_123 --wait-seconds 600 --json'),
    ).toMatchObject({
      kind: 'order',
      detail: 'ord_123',
    })
    expect(parseTradeCommand('cd /tmp && agentos trade portfolio --json')).toMatchObject({
      kind: 'portfolio',
    })
  })
  it('ignores everything else', () => {
    expect(parseTradeCommand('ls -la')).toBeNull()
    expect(parseTradeCommand('agentos skills list')).toBeNull()
    expect(parseTradeCommand('')).toBeNull()
  })
})

describe('parseTradeResult', () => {
  it('describes a projected-away result from the call itself, without error styling', () => {
    const call = parseTradeCommand(
      'agentos trade quote --chain base --in ETH --out USDC --amount 0.0001',
    )!
    const out = parseTradeResult(
      call,
      '[tool_result_projection]\ntool_result_handle: tr-809b\nsha256: cbf9e8',
    )
    expect(out.summary).toBe(call.detail)
    expect(out.error).toBeNull()
  })

  const swap = parseTradeCommand(
    'agentos trade swap --chain base --in ETH --out USDC --amount 0.01',
  )!

  it('reads a single order and earns the stamps only on proof', () => {
    const awaiting = parseTradeResult(
      swap,
      'exit_code=0\n' +
        JSON.stringify({
          orders: [
            {
              orderId: 'o1',
              status: 'awaiting_approval',
              amountIn: '0.01',
              tokenIn: { symbol: 'ETH' },
              tokenOut: { symbol: 'USDC' },
              expectedOut: '25.1',
              provider: 'uniswap',
            },
          ],
        }),
    )
    expect(awaiting.awaiting).toBe(true)
    expect(awaiting.confirmed).toBe(false)
    expect(awaiting.orderId).toBe('o1')
    expect(awaiting.provider).toBe('uniswap')
    expect(awaiting.summary).toBe('0.01 ETH → 25.1 USDC · awaiting approval')

    const confirmedNoHash = parseTradeResult(
      swap,
      JSON.stringify({
        orders: [
          { orderId: 'o2', status: 'confirmed', amountIn: '1', tokenIn: 'ETH', tokenOut: 'USDC' },
        ],
      }),
    )
    expect(confirmedNoHash.confirmed).toBe(false)

    const confirmed = parseTradeResult(
      swap,
      JSON.stringify({
        orders: [
          {
            orderId: 'o3',
            status: 'confirmed',
            txHash: '0xabc',
            explorerUrl: 'https://x/tx/0xabc',
            amountIn: '1',
            tokenIn: 'ETH',
            tokenOut: 'USDC',
          },
        ],
      }),
    )
    expect(confirmed.confirmed).toBe(true)
    expect(confirmed.txHash).toBe('0xabc')
    expect(confirmed.explorerUrl).toBe('https://x/tx/0xabc')
  })

  it('summarises a batch, a quote, balances and a portfolio', () => {
    const batch = parseTradeResult(
      swap,
      JSON.stringify({
        orders: [{ status: 'confirmed', txHash: '0x1' }, { status: 'awaiting_approval' }],
      }),
    )
    expect(batch.summary).toBe('2 wallets · 1 confirmed, 1 awaiting approval')
    expect(batch.awaiting).toBe(true)

    const quote = parseTradeResult(
      parseTradeCommand('agentos trade quote --in ETH --out USDC --amount 0.1')!,
      JSON.stringify({
        amountIn: '0.1',
        tokenIn: { symbol: 'ETH' },
        amountOut: '250',
        tokenOut: { symbol: 'USDC' },
        priceImpactPct: 0.12,
        guard: { decision: 'needs_approval' },
        provider: 'aggregator',
      }),
    )
    expect(quote.summary).toBe('0.1 ETH → 250 USDC · impact 0.12% · would need approval')
    expect(quote.provider).toBe('aggregator')

    const balances = parseTradeResult(
      parseTradeCommand('agentos wallet balances --json')!,
      JSON.stringify({
        balances: [
          { amount: '0.5', valueUsd: 1200 },
          { amount: '0', valueUsd: 0 },
        ],
        updatedAt: 1_700_000_000_000,
      }),
    )
    expect(balances.summary).toBe('1 balances · $1,200.00')
    expect(balances.marketAt).toBe(1_700_000_000_000)

    const portfolio = parseTradeResult(
      parseTradeCommand('agentos trade portfolio --json')!,
      JSON.stringify({ totals: { valueUsd: 42.5 }, holdings: [{}, {}] }),
    )
    expect(portfolio.summary).toBe('$42.50 · 2 holdings')
  })

  it('falls back to the first line and reports a failing exit code', () => {
    const plain = parseTradeResult(swap, 'exit_code=0\nnothing to see here\nsecond line')
    expect(plain.summary).toBe('nothing to see here')
    expect(plain.error).toBeNull()
    const failed = parseTradeResult(swap, 'exit_code=1\nError: gateway unreachable')
    expect(failed.error).toBe('Error: gateway unreachable')
    const rpcError = parseTradeResult(
      swap,
      JSON.stringify({
        error: { message: 'No Uniswap API key configured', code: 'trading.no_api_key' },
      }),
    )
    expect(rpcError.error).toBe('No Uniswap API key configured')
    expect(exitCodeOf('exit_code=2\n')).toBe(2)
    expect(exitCodeOf('{}')).toBeNull()
  })
})

describe('ledgerRuns', () => {
  it('folds runs of three or more, leaves shorter runs alone', () => {
    expect(ledgerRuns([true, true, true, false, true, true])).toEqual([{ start: 0, length: 3 }])
    expect(ledgerRuns([false, true, true, true, true])).toEqual([{ start: 1, length: 4 }])
    expect(ledgerRuns([true, false, true])).toEqual([])
  })
})

describe('sends, allowances, decode and network rows', () => {
  const A = '0x2222222222222222222222222222222222222222'
  const B = '0x3333333333333333333333333333333333333333'

  it('recognises the new commands and summarises their arguments', () => {
    expect(
      parseTradeCommand(
        `agentos trade send --chain base --token USDC --to ${A} --amount 25 --json`,
      ),
    ).toMatchObject({ kind: 'send', title: 'Send', detail: '25 USDC → 0x2222…2222 · Base' })
    expect(
      parseTradeCommand(
        `agentos trade send --chain base --token ETH --to ${A}=0.1 --to ${B}=0.2 --json`,
      ),
    ).toMatchObject({ kind: 'send', detail: 'ETH → 2 recipients · Base' })
    expect(
      parseTradeCommand(
        'agentos trade send --chain robinhood --token USDG --file list.txt --usd 5',
      ),
    ).toMatchObject({ kind: 'send', detail: '$5 of USDG → a list · Robinhood' })
    expect(parseTradeCommand('agentos trade allowances --json')).toMatchObject({
      kind: 'allowances',
      title: 'Allowances',
    })
    expect(
      parseTradeCommand(`agentos trade revoke --chain base --token ${A} --spender ${B} --json`),
    ).toMatchObject({ kind: 'revoke', detail: '0x2222…2222 for 0x3333…3333 · Base' })
    expect(
      parseTradeCommand(`agentos trade decode --chain base 0x${'ab'.repeat(32)} --json`),
    ).toMatchObject({ kind: 'decode', detail: '0xabababab… · Base' })
    expect(parseTradeCommand('agentos trade decode --chain base --data 0xa9 --json')).toMatchObject(
      { kind: 'decode', detail: 'calldata · Base' },
    )
    expect(parseTradeCommand('agentos trade network --json')).toMatchObject({ kind: 'network' })
  })

  it('reads a send order and a multisend batch', () => {
    const send = parseTradeCommand(`agentos trade send --chain base --token USDC --to ${A}`)!
    const single = parseTradeResult(
      send,
      JSON.stringify({
        orders: [
          {
            orderId: 's1',
            kind: 'send',
            status: 'awaiting_approval',
            amountIn: '25',
            tokenIn: { symbol: 'USDC' },
            tokenOut: { symbol: 'USDC' },
            recipient: A,
          },
        ],
        batchId: null,
      }),
    )
    expect(single.summary).toBe('25 USDC → 0x2222…2222 · awaiting approval')
    expect(single.awaiting).toBe(true)
    expect(single.orderId).toBe('s1')
    const batch = parseTradeResult(
      send,
      JSON.stringify({
        orders: [
          { orderId: 'a', kind: 'send', status: 'confirmed', txHash: '0x1', amountIn: '1' },
          { orderId: 'b', kind: 'send', status: 'awaiting_approval', amountIn: '2' },
          { orderId: 'c', kind: 'send', status: 'awaiting_approval', amountIn: '3' },
        ],
        batchId: 'bat_1',
      }),
    )
    expect(batch.summary).toBe('3 recipients · 1 confirmed, 2 awaiting approval')
    expect(batch.awaiting).toBe(true)
    expect(batch.confirmed).toBe(false)
    // The stamp jumps to the first leg still waiting.
    expect(batch.orderId).toBe('b')
  })

  it('reads a revoke, an allowance review, a decode and a network probe', () => {
    const revoke = parseTradeCommand('agentos trade revoke --chain base --token x --spender y')!
    expect(
      parseTradeResult(
        revoke,
        JSON.stringify({
          order: {
            orderId: 'r1',
            kind: 'revoke',
            status: 'awaiting_approval',
            amountIn: 'unlimited',
            tokenIn: { symbol: 'USDC' },
            recipient: A,
            recipientLabel: 'Permit2',
          },
        }),
      ).summary,
    ).toBe('revoke USDC for Permit2 · awaiting approval')
    const allowances = parseTradeCommand('agentos trade allowances --json')!
    expect(
      parseTradeResult(
        allowances,
        JSON.stringify({
          allowances: [
            { unlimited: true, exposureUsd: 1000 },
            { unlimited: false, exposureUsd: 12.5 },
          ],
          unlimitedCount: 1,
        }),
      ).summary,
    ).toBe('2 live · 1 unlimited · $1,012.50 at stake')
    const decode = parseTradeCommand('agentos trade decode --chain base 0xabc')!
    const known = parseTradeResult(
      decode,
      JSON.stringify({
        call: { function: 'transfer', selector: '0xa9059cbb', known: true },
        tx: { status: 'success', hash: '0x' + 'ab'.repeat(32) },
        transfers: [{}],
      }),
    )
    expect(known.summary).toBe('transfer · success · 1 transfer')
    expect(known.txHash).toBe('0x' + 'ab'.repeat(32))
    expect(
      parseTradeResult(
        decode,
        JSON.stringify({ call: { function: null, selector: '0xdeadbeef', known: false } }),
      ).summary,
    ).toBe('0xdeadbeef (unknown)')
    const network = parseTradeCommand('agentos trade network --json')!
    const probe = parseTradeResult(
      network,
      JSON.stringify({
        chains: [
          { name: 'Base', healthy: true, blockAgeS: 2 },
          { name: 'Robinhood Chain', healthy: false, blockAgeS: 90 },
        ],
      }),
    )
    expect(probe.summary).toBe('Base ✓ 2s · Robinhood Chain ✗ 90s')
    expect(probe.error).toBe('1 chain unhealthy')
  })
})
