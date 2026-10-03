import { act, fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { DesktopApi } from '@shared/ipc'
import { QUICK_ASK_MAX_BYTES } from '@shared/quick-ask'
import { desktopApi, resetDesktopApiForTests } from '~/lib/desktop-api'
import { isQuickAskWindow, QuickAskView } from './QuickAskView'

/** The panel half of the bridge, recorded; everything else is the browser fallback. */
function bridge(submitResult = true) {
  resetDesktopApiForTests()
  const fallback = desktopApi()
  let shown: (() => void) | null = null
  const quickAsk = {
    ...fallback.quickAsk,
    submit: vi.fn(async () => submitResult),
    hide: vi.fn(async () => {}),
    ready: vi.fn(async () => {}),
    resize: vi.fn(async () => {}),
    onShown: vi.fn((listener: () => void) => {
      shown = listener
      return () => {
        shown = null
      }
    }),
  }
  window.agentos = { ...fallback, quickAsk } as DesktopApi
  return { quickAsk, show: () => act(() => shown?.()) }
}

const field = () => screen.getByRole('textbox', { name: 'Ask AgentOS' }) as HTMLTextAreaElement

function type(text: string) {
  fireEvent.change(field(), { target: { value: text } })
}

async function press(init: KeyboardEventInit & { key: string }) {
  await act(async () => {
    fireEvent.keyDown(field(), init)
  })
}

beforeEach(() => {
  delete window.agentos
  localStorage.clear()
})

describe('QuickAskView', () => {
  it('tells main it is ready, so the panel is never shown before it renders', () => {
    const { quickAsk } = bridge()
    render(<QuickAskView />)
    expect(quickAsk.ready).toHaveBeenCalledTimes(1)
    expect(screen.getByTestId('quick-ask')).toBeInTheDocument()
    expect(document.documentElement.dataset.window).toBe('quick-ask')
  })

  it('Return sends to a new chat and clears the field', async () => {
    const { quickAsk } = bridge()
    render(<QuickAskView />)
    type('what moved ETH today?')
    await press({ key: 'Enter' })
    expect(quickAsk.submit).toHaveBeenCalledWith({ text: 'what moved ETH today?', target: 'new' })
    expect(field().value).toBe('')
    expect(quickAsk.hide).not.toHaveBeenCalled()
  })

  it('Option-Return sends to the current chat', async () => {
    const { quickAsk } = bridge()
    render(<QuickAskView />)
    type('and on Base?')
    await press({ key: 'Enter', altKey: true })
    expect(quickAsk.submit).toHaveBeenCalledWith({ text: 'and on Base?', target: 'current' })
    expect(field().value).toBe('')
  })

  it('Escape closes without sending and keeps the text for next time', async () => {
    const { quickAsk, show } = bridge()
    render(<QuickAskView />)
    type('half a thought')
    await press({ key: 'Escape' })
    expect(quickAsk.hide).toHaveBeenCalledTimes(1)
    expect(quickAsk.submit).not.toHaveBeenCalled()
    expect(field().value).toBe('half a thought')
    // Shown again: the text is still there and the field has the keyboard.
    field().blur()
    show()
    expect(field()).toHaveFocus()
    expect(field().value).toBe('half a thought')
  })

  it('Return on empty or whitespace-only text does nothing', async () => {
    const { quickAsk } = bridge()
    render(<QuickAskView />)
    await press({ key: 'Enter' })
    type('   \n  ')
    await press({ key: 'Enter' })
    await press({ key: 'Enter', altKey: true })
    expect(quickAsk.submit).not.toHaveBeenCalled()
  })

  it('Shift-Return is a new line, not a send', async () => {
    const { quickAsk } = bridge()
    render(<QuickAskView />)
    type('line one')
    await press({ key: 'Enter', shiftKey: true })
    expect(quickAsk.submit).not.toHaveBeenCalled()
  })

  it('leaves Return and Escape to an input method mid-composition', async () => {
    const { quickAsk } = bridge()
    render(<QuickAskView />)
    type('にほん')
    await press({ key: 'Enter', isComposing: true })
    await press({ key: 'Escape', keyCode: 229 })
    expect(quickAsk.submit).not.toHaveBeenCalled()
    expect(quickAsk.hide).not.toHaveBeenCalled()
  })

  it('refuses text over 20 kB in place, before it reaches main', async () => {
    const { quickAsk } = bridge()
    render(<QuickAskView />)
    type('a'.repeat(QUICK_ASK_MAX_BYTES + 1))
    expect(screen.getByText(/Too long for Quick Ask/)).toBeInTheDocument()
    await press({ key: 'Enter' })
    expect(quickAsk.submit).not.toHaveBeenCalled()
  })

  it('keeps the text when main refuses the submission', async () => {
    const { quickAsk } = bridge(false)
    render(<QuickAskView />)
    type('try me')
    await press({ key: 'Enter' })
    expect(quickAsk.submit).toHaveBeenCalledTimes(1)
    expect(field().value).toBe('try me')
    expect(screen.getByText(/did not go through/)).toBeInTheDocument()
  })

  it('shows the key hint line', () => {
    bridge()
    render(<QuickAskView />)
    expect(screen.getByText('new chat')).toBeInTheDocument()
    expect(screen.getByText('current chat')).toBeInTheDocument()
    expect(screen.getByText('close')).toBeInTheDocument()
  })
})

describe('isQuickAskWindow', () => {
  it('is the panel route only', () => {
    expect(isQuickAskWindow('#/quick-ask')).toBe(true)
    expect(isQuickAskWindow('#/quick-ask?x=1')).toBe(true)
    expect(isQuickAskWindow('')).toBe(false)
    expect(isQuickAskWindow('#/sessions')).toBe(false)
    expect(isQuickAskWindow('#/quick-asking')).toBe(false)
  })
})
