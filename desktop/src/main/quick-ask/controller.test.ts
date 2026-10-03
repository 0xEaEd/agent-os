// @vitest-environment node
import { describe, expect, it, vi } from 'vitest'
import { IPC } from '@shared/ipc'
import { QUICK_ASK_MAX_BYTES } from '@shared/quick-ask'
import { QuickAskController, type MainWindowLike, type PanelLike } from './controller'

const PANEL = { id: 'panel' }
const MAIN = { id: 'main' }

function fakePanel(): PanelLike & Record<string, ReturnType<typeof vi.fn>> {
  return {
    ensure: vi.fn(),
    toggle: vi.fn(),
    hide: vi.fn(),
    destroy: vi.fn(),
    owns: vi.fn((sender: unknown) => sender === PANEL),
    markReady: vi.fn(),
    resize: vi.fn(),
  }
}

function fakeWindow(opts: { loading?: boolean; minimized?: boolean } = {}) {
  return {
    isMinimized: vi.fn(() => opts.minimized ?? false),
    restore: vi.fn(),
    show: vi.fn(),
    focus: vi.fn(),
    webContents: { isLoading: vi.fn(() => opts.loading ?? false), send: vi.fn() },
  } satisfies MainWindowLike
}

function setup(main: MainWindowLike | null = fakeWindow(), taken: string[] = []) {
  const panel = fakePanel()
  const held = new Map<string, () => void>()
  const deps = {
    registry: {
      register: vi.fn((acc: string, cb: () => void) => {
        if (taken.includes(acc)) return false
        held.set(acc, cb)
        return true
      }),
      unregister: vi.fn((acc: string) => void held.delete(acc)),
    },
    panel,
    findMainWindow: vi.fn(() => main),
    openMainWindow: vi.fn(),
    focusApp: vi.fn(),
    broadcast: vi.fn(),
  }
  return { qa: new QuickAskController(deps), deps, panel, held, main }
}

describe('QuickAskController', () => {
  it('keeps the panel created while the key is registered, and drops it when off', () => {
    const { qa, panel, held } = setup()
    qa.apply({ enabled: true, shortcut: 'Alt+Space' })
    expect(panel.ensure).toHaveBeenCalled()
    held.get('Alt+Space')!()
    expect(panel.toggle).toHaveBeenCalledTimes(1)
    qa.apply({ enabled: false, shortcut: 'Alt+Space' })
    expect(panel.destroy).toHaveBeenCalled()
    expect(held.size).toBe(0)
  })

  it('broadcasts a refused key so Settings can say so', () => {
    const { qa, deps } = setup(fakeWindow(), ['Control+Space'])
    qa.apply({ enabled: true, shortcut: 'Control+Space' })
    expect(qa.status()).toEqual({ state: 'unavailable', shortcut: 'Control+Space' })
    expect(deps.broadcast).toHaveBeenCalledWith(IPC.quickAsk.statusChanged, {
      state: 'unavailable',
      shortcut: 'Control+Space',
    })
  })

  it('a submission hides the panel, brings the main window forward and pings it', () => {
    const main = fakeWindow({ minimized: true })
    const { qa, panel, deps } = setup(main)
    expect(qa.submit(PANEL, { text: ' hello ', target: 'new' })).toBe(true)
    expect(panel.hide).toHaveBeenCalled()
    expect(main.restore).toHaveBeenCalled()
    expect(main.show).toHaveBeenCalled()
    expect(main.focus).toHaveBeenCalled()
    expect(deps.focusApp).toHaveBeenCalled()
    expect(main.webContents.send).toHaveBeenCalledWith(IPC.quickAsk.deliver)
    // The window collects it; only once.
    expect(qa.take(MAIN)).toEqual([{ text: 'hello', target: 'new' }])
    expect(qa.take(MAIN)).toEqual([])
  })

  it('opens a main window when there is none; the new window collects on mount', () => {
    const { qa, deps } = setup(null)
    expect(qa.submit(PANEL, { text: 'hi', target: 'current' })).toBe(true)
    expect(deps.openMainWindow).toHaveBeenCalledTimes(1)
    expect(qa.inbox.size).toBe(1)
    expect(qa.take(MAIN)).toEqual([{ text: 'hi', target: 'current' }])
  })

  it('does not ping a window that is still loading; it collects on mount', () => {
    const main = fakeWindow({ loading: true })
    const { qa } = setup(main)
    qa.submit(PANEL, { text: 'hi', target: 'new' })
    expect(main.webContents.send).not.toHaveBeenCalled()
    expect(qa.inbox.size).toBe(1)
  })

  it('refuses a malformed or oversized payload and leaves the panel up', () => {
    const { qa, panel, main } = setup()
    for (const raw of [
      null,
      'hello',
      { text: 42, target: 'new' },
      { text: 'hi', target: 'elsewhere' },
      { text: '   ', target: 'new' },
      { text: 'x'.repeat(QUICK_ASK_MAX_BYTES + 1), target: 'new' },
    ]) {
      expect(qa.submit(PANEL, raw)).toBe(false)
    }
    expect(panel.hide).not.toHaveBeenCalled()
    expect(main!.webContents.send).not.toHaveBeenCalled()
    expect(qa.inbox.size).toBe(0)
  })

  it('answers the panel-only calls for the panel alone', () => {
    const { qa, panel } = setup()
    expect(qa.submit(MAIN, { text: 'hi', target: 'new' })).toBe(false)
    qa.hide(MAIN)
    qa.ready(MAIN)
    qa.resize(MAIN, 200)
    expect(panel.hide).not.toHaveBeenCalled()
    expect(panel.markReady).not.toHaveBeenCalled()
    expect(panel.resize).not.toHaveBeenCalled()
    qa.hide(PANEL)
    qa.ready(PANEL)
    qa.resize(PANEL, 200)
    expect(panel.hide).toHaveBeenCalledTimes(1)
    expect(panel.markReady).toHaveBeenCalledTimes(1)
    expect(panel.resize).toHaveBeenCalledWith(200)
  })

  it('never hands the inbox to the panel', () => {
    const { qa } = setup()
    qa.submit(PANEL, { text: 'hi', target: 'new' })
    expect(qa.take(PANEL)).toEqual([])
    expect(qa.inbox.size).toBe(1)
  })
})
