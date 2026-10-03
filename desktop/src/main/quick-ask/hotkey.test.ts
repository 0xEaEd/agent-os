// @vitest-environment node
import { describe, expect, it, vi } from 'vitest'
import { QuickAskHotkey, type ShortcutRegistry } from './hotkey'

/** globalShortcut, with keys another app already holds. */
function fakeRegistry(taken: string[] = []) {
  const held = new Map<string, () => void>()
  const registry: ShortcutRegistry & { held: Map<string, () => void> } = {
    held,
    register: vi.fn((accelerator: string, callback: () => void) => {
      if (taken.includes(accelerator) || held.has(accelerator)) return false
      held.set(accelerator, callback)
      return true
    }),
    unregister: vi.fn((accelerator: string) => {
      held.delete(accelerator)
    }),
  }
  return registry
}

describe('QuickAskHotkey', () => {
  it('registers the chosen key, and the key toggles the panel', () => {
    const registry = fakeRegistry()
    const onPress = vi.fn()
    const hotkey = new QuickAskHotkey(registry, onPress)
    expect(hotkey.apply({ enabled: true, shortcut: 'Alt+Space' })).toEqual({
      state: 'ready',
      shortcut: 'Alt+Space',
    })
    registry.held.get('Alt+Space')!()
    expect(onPress).toHaveBeenCalledTimes(1)
  })

  it('turning it off releases the key at once', () => {
    const registry = fakeRegistry()
    const hotkey = new QuickAskHotkey(registry, () => {})
    hotkey.apply({ enabled: true, shortcut: 'Alt+Space' })
    expect(hotkey.apply({ enabled: false, shortcut: 'Alt+Space' }).state).toBe('off')
    expect(registry.unregister).toHaveBeenCalledWith('Alt+Space')
    expect(registry.held.size).toBe(0)
  })

  it('changing the key swaps the registration', () => {
    const registry = fakeRegistry()
    const hotkey = new QuickAskHotkey(registry, () => {})
    hotkey.apply({ enabled: true, shortcut: 'Alt+Space' })
    hotkey.apply({ enabled: true, shortcut: 'CommandOrControl+Shift+Space' })
    expect([...registry.held.keys()]).toEqual(['CommandOrControl+Shift+Space'])
  })

  it('reports a key another app holds as unavailable, and says so once', () => {
    const registry = fakeRegistry(['Control+Space'])
    const onStatus = vi.fn()
    const hotkey = new QuickAskHotkey(registry, () => {}, onStatus)
    expect(hotkey.apply({ enabled: true, shortcut: 'Control+Space' })).toEqual({
      state: 'unavailable',
      shortcut: 'Control+Space',
    })
    expect(onStatus).toHaveBeenLastCalledWith({ state: 'unavailable', shortcut: 'Control+Space' })
    // Retried on the next apply (the other app may have let go), no new news.
    hotkey.apply({ enabled: true, shortcut: 'Control+Space' })
    expect(registry.register).toHaveBeenCalledTimes(2)
    expect(onStatus).toHaveBeenCalledTimes(1)
  })

  it('treats a register that throws as unavailable rather than crashing', () => {
    const registry = fakeRegistry()
    registry.register = vi.fn(() => {
      throw new Error('bad accelerator')
    })
    const hotkey = new QuickAskHotkey(registry, () => {})
    expect(hotkey.apply({ enabled: true, shortcut: 'Alt+Space' }).state).toBe('unavailable')
  })

  it('a write to another settings section changes nothing', () => {
    const registry = fakeRegistry()
    const onStatus = vi.fn()
    const hotkey = new QuickAskHotkey(registry, () => {}, onStatus)
    hotkey.apply({ enabled: true, shortcut: 'Alt+Space' })
    hotkey.apply({ enabled: true, shortcut: 'Alt+Space' })
    expect(registry.register).toHaveBeenCalledTimes(1)
    expect(registry.unregister).not.toHaveBeenCalled()
    expect(onStatus).toHaveBeenCalledTimes(1)
  })

  it('release lets go of the key on quit', () => {
    const registry = fakeRegistry()
    const hotkey = new QuickAskHotkey(registry, () => {})
    hotkey.apply({ enabled: true, shortcut: 'Alt+Space' })
    hotkey.release()
    hotkey.release()
    expect(registry.unregister).toHaveBeenCalledTimes(1)
    expect(registry.held.size).toBe(0)
  })
})
