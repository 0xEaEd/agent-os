// Plain text for a rendered chat bubble, with its visible line breaks kept.
//
// `textContent` is the obvious read and the wrong one for markdown output: a
// `<br>` contributes nothing and adjacent blocks (`<p>`, `<li>`, headings) run
// together, so a reply shown as five lines lands on the clipboard as one. This
// is a small `innerText`: it walks the DOM and puts line breaks where the
// renderer draws them. `innerText` itself cannot be used because the bubble is
// read from a detached clone (the action chrome is stripped first), and a node
// that is not rendered reports its `textContent`.

/** Blocks separated from their siblings by a blank line. */
const PARAGRAPH_TAGS = new Set([
  'P',
  'H1',
  'H2',
  'H3',
  'H4',
  'H5',
  'H6',
  'UL',
  'OL',
  'PRE',
  'BLOCKQUOTE',
  'TABLE',
  'HR',
  'DL',
  'DETAILS',
])

/** Blocks that start on their own line without a blank line around them. */
const LINE_TAGS = new Set([
  'DIV',
  'LI',
  'TR',
  'DT',
  'DD',
  'SUMMARY',
  'SECTION',
  'ARTICLE',
  'HEADER',
  'FOOTER',
  'FIGURE',
  'FIGCAPTION',
  'CAPTION',
])

const SKIPPED_TAGS = new Set(['SCRIPT', 'STYLE', 'TEMPLATE', 'BUTTON', 'SVG'])

/** Containers whose whitespace-only text is source formatting, never content. */
const STRUCTURAL_TAGS = new Set(['UL', 'OL', 'DL', 'TABLE', 'THEAD', 'TBODY', 'TFOOT', 'TR'])

/**
 * Text of `root` as it reads on screen.
 *
 * `preserveWhitespace` is for bubbles rendered with `white-space: pre-wrap`
 * (user, system and error rows hold their text verbatim); otherwise runs of
 * whitespace collapse the way the browser collapses them, except inside `<pre>`.
 */
export function renderedPlainText(root: Node, preserveWhitespace = false): string {
  let out = ''
  // Newlines owed before the next visible text; paid lazily so a block at the
  // very start or end of the bubble adds nothing.
  let owed = 0

  const atLineStart = () => out === '' || out.endsWith('\n')

  function pay(): void {
    if (!owed || !out) {
      owed = 0
      return
    }
    out = out.replace(/[ \t]+$/, '')
    const have = /\n*$/.exec(out)![0].length
    if (owed > have) out += '\n'.repeat(owed - have)
    owed = 0
  }

  function emit(text: string): void {
    if (!text) return
    pay()
    out += text
  }

  function owe(lines: number): void {
    owed = Math.max(owed, lines)
  }

  function listMarker(item: Element): string {
    const list = item.parentElement
    let depth = 0
    for (let el = list?.parentElement ?? null; el && el !== root; el = el.parentElement) {
      if (el.tagName === 'UL' || el.tagName === 'OL') depth += 1
    }
    const indent = '  '.repeat(depth)
    if (list?.tagName === 'OL') {
      const start = Number.parseInt(list.getAttribute('start') || '1', 10) || 1
      const index = [...list.children].filter((c) => c.tagName === 'LI').indexOf(item)
      return `${indent}${start + index}. `
    }
    if (list?.tagName === 'UL') return `${indent}- `
    return ''
  }

  function walk(node: Node, preserve: boolean): void {
    if (node.nodeType === Node.TEXT_NODE) {
      let text = (node as Text).data
      if (!preserve) {
        const parent = node.parentElement?.tagName.toUpperCase() ?? ''
        if (STRUCTURAL_TAGS.has(parent) && !text.trim()) return
        text = text.replace(/[ \t\n\r\f]+/g, ' ')
        if (owed || atLineStart() || out.endsWith(' ')) text = text.replace(/^ /, '')
      }
      emit(text)
      return
    }
    if (node.nodeType !== Node.ELEMENT_NODE && node.nodeType !== Node.DOCUMENT_FRAGMENT_NODE) {
      return
    }
    const el = node as Element
    const tag = (el.tagName || '').toUpperCase()
    if (SKIPPED_TAGS.has(tag)) return
    if (tag === 'BR') {
      pay()
      out = out.replace(/[ \t]+$/, '') + '\n'
      return
    }
    if (tag === 'IMG') {
      emit(el.getAttribute('alt') || '')
      return
    }
    if (tag === 'HR') {
      owe(2)
      emit('---')
      owe(2)
      return
    }

    const nestedList =
      (tag === 'UL' || tag === 'OL') &&
      el.parentElement !== null &&
      el.parentElement.tagName === 'LI'
    const gap = nestedList ? 1 : PARAGRAPH_TAGS.has(tag) ? 2 : LINE_TAGS.has(tag) ? 1 : 0
    if (gap) owe(gap)
    if (tag === 'LI') emit(listMarker(el))
    if ((tag === 'TD' || tag === 'TH') && el.previousElementSibling) emit('\t')

    const childPreserve = preserve || tag === 'PRE' || tag === 'TEXTAREA'
    el.childNodes.forEach((child) => walk(child, childPreserve))

    if (gap) owe(gap)
  }

  root.childNodes.forEach((child) => walk(child, preserveWhitespace))
  return out.trim()
}
