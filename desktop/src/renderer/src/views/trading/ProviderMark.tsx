import agentos from '~/assets/providers/agentos.svg?raw'
import uniswap from '~/assets/providers/uniswap.svg?raw'
import { cn } from '~/lib/utils'

/**
 * The swap route's brand mark. Both are inlined monochrome SVGs so they take
 * `currentColor` — the desk tints them with whatever the row already uses,
 * and the window's CSP allows no remote images anyway. A provider we have no
 * mark for gets nothing back, so every caller keeps its own fallback.
 */
const MARKS: Record<string, string> = { aggregator: agentos, uniswap }

export function providerMark(id: string | null | undefined): string | null {
  return (id && MARKS[id]) || null
}

export function ProviderMark({
  id,
  size = 13,
  className,
}: {
  id: string | null | undefined
  size?: number
  className?: string
}) {
  const mark = providerMark(id)
  if (!mark) return null
  return (
    <span
      className={cn('trd-mark', className)}
      style={{ width: size, height: size }}
      aria-hidden
      // Static, bundled SVG from assets/providers: not user content.
      dangerouslySetInnerHTML={{ __html: mark }}
    />
  )
}
