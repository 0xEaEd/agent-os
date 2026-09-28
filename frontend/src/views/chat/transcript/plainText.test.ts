import { describe, expect, it } from 'vitest'
import { render } from '../markdown'
import { renderedPlainText } from './plainText'

function fromMarkdown(markdown: string): string {
  const body = document.createElement('div')
  body.innerHTML = render(markdown)
  body.querySelectorAll('.code-block-header').forEach((node) => node.remove())
  return renderedPlainText(body)
}

describe('renderedPlainText', () => {
  it('keeps the soft line breaks a reply is drawn with', () => {
    const reply = [
      'AgentOS 2026.9.28 🧢',
      '🦄 Uniswap V4 LP: read and manage positions',
      '🔐 Safer LP transactions with simulation and approval',
      '🍎 Native Mac and Windows portable builds',
    ].join('\n')

    expect(fromMarkdown(reply)).toBe(reply)
  })

  it('separates paragraphs and headings with a blank line', () => {
    expect(fromMarkdown('# Title\n\nFirst paragraph.\n\nSecond paragraph.')).toBe(
      'Title\n\nFirst paragraph.\n\nSecond paragraph.',
    )
  })

  it('writes list markers the CSS draws, including numbering and nesting', () => {
    expect(fromMarkdown('Steps:\n\n1. one\n2. two\n   - nested\n3. three\n\nDone.')).toBe(
      'Steps:\n\n1. one\n2. two\n  - nested\n3. three\n\nDone.',
    )
    expect(fromMarkdown('- a\n- b')).toBe('- a\n- b')
  })

  it('keeps fenced code verbatim and collapses markup whitespace elsewhere', () => {
    expect(fromMarkdown('Run:\n\n```bash\nuv sync\n  --all-extras\n```\n\nthen   **go**.')).toBe(
      'Run:\n\nuv sync\n  --all-extras\n\nthen go.',
    )
  })

  it('tab-separates table cells one row per line', () => {
    expect(fromMarkdown('| a | b |\n|---|---|\n| 1 | 2 |')).toBe('a\tb\n1\t2')
  })

  it('returns pre-wrap text verbatim when asked to preserve whitespace', () => {
    const body = document.createElement('div')
    body.textContent = 'line one\n\n  indented line'
    expect(renderedPlainText(body, true)).toBe('line one\n\n  indented line')
  })
})
