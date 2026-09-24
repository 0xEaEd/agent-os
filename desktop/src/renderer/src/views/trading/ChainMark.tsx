import { chainName, chainShort } from './logic'

/**
 * The chain's own mark, drawn rather than fetched.
 *
 * Both are inline SVG so a chain badge costs no request, survives an offline
 * launch and stays crisp at 12 px, where a downscaled PNG would not. Base's
 * mark is its current brand square (a rounded square in Base blue, which is
 * the whole logo — there is nothing inside it); Robinhood's is the feather,
 * set in a tile of the same silhouette so the two read at the same weight in
 * a column. An unknown chain gets a neutral tile with its initial, never a
 * guessed logo.
 */
export function ChainMark({ chainId, className }: { chainId: number; className?: string }) {
  const common = {
    viewBox: '0 0 24 24',
    className: ['trd-chainmark', className].filter(Boolean).join(' '),
    role: 'img' as const,
    'aria-label': chainName(chainId),
  }
  // 1.9/24 is the 101.12/1280 corner of the Base square, to scale.
  if (chainId === 8453) {
    return (
      <svg {...common}>
        <rect width="24" height="24" rx="1.9" fill="#0052FF" />
      </svg>
    )
  }
  if (chainId === 4663) {
    return (
      <svg {...common}>
        <rect width="24" height="24" rx="1.9" fill="#00C805" />
        <path
          transform="translate(4.6 4.6) scale(0.617)"
          fill="#fff"
          d="M2.84 24h.53c.096 0 .192-.048.224-.128C7.591 13.696 11.94 8.656 14.67 5.638c.112-.128.064-.225-.096-.225h-4.88a.55.55 0 0 0-.45.225L5.746 9.972c-.514.642-.642 1.236-.642 2.086v4.43c-1.14 3.194-1.862 5.361-2.392 7.32-.032.125.016.192.129.192M20.447.646c-.754-.802-4.157-.834-5.73-.224a3 3 0 0 0-.786.465 41 41 0 0 0-3.323 3.178c-.112.113-.064.225.097.225h5.409c.497 0 .786.289.786.786v6.1c0 .16.128.208.225.064l3.258-4.254c.53-.69.69-.898.835-1.861.192-1.413.08-3.58-.77-4.479m-6.982 16.18 2.231-3.676a.7.7 0 0 0 .064-.29V6.73c0-.16-.112-.225-.224-.097-3.355 3.74-5.971 7.672-8.395 12.407-.06.12.016.225.16.177l5.009-1.54c.565-.174.882-.402 1.155-.852"
        />
      </svg>
    )
  }
  return (
    <svg {...common}>
      <rect width="24" height="24" rx="1.9" fill="currentColor" opacity="0.28" />
      <text x="12" y="17" textAnchor="middle" fontSize="14" fontWeight="600" fill="currentColor">
        {chainShort(chainId).slice(0, 1)}
      </text>
    </svg>
  )
}

/** The mark and the chain's short name, as one inline badge. */
export function ChainBadge({ chainId, className }: { chainId: number; className?: string }) {
  return (
    <span className={['trd-chainbadge', className].filter(Boolean).join(' ')}>
      <ChainMark chainId={chainId} />
      {chainShort(chainId)}
    </span>
  )
}
