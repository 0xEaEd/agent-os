import { create } from 'zustand'
import { isTradingAgentKey } from '~/views/trading/desk/agent'
import { BOOK_DEFAULT, BOOK_MAX, BOOK_MIN } from '~/views/trading/desk/desk-logic'

/**
 * The desk's own chrome state: the BOOK's width and whether it is open, the
 * tab it shows, and the full-desk toggle. Width and open-ness persist; the
 * tab and mode are per launch.
 */

const WIDTH_KEY = 'agentos-desktop.trading.bookWidth'
const OPEN_KEY = 'agentos-desktop.trading.bookOpen'
const SESSION_KEY = 'agentos-desktop.trading.sessionKey'
const FILED_KEY = 'agentos-desktop.trading.sessionFiled'
/** The spec version of the `trading` agent this desktop last wrote to the gateway. */
const AGENT_VERSION_KEY = 'agentos-desktop.trading.agentVersion'
/** The chat the user left to enter Trading mode; the pill's "Chat" goes back there. */
const RETURN_KEY = 'agentos-desktop.trading.returnSession'

function loadWidth(): number {
  try {
    const raw = Number(localStorage.getItem(WIDTH_KEY))
    if (Number.isFinite(raw) && raw >= BOOK_MIN && raw <= BOOK_MAX) return raw
  } catch {
    /* storage unavailable */
  }
  return BOOK_DEFAULT
}

function loadOpen(): boolean {
  try {
    return localStorage.getItem(OPEN_KEY) !== 'false'
  } catch {
    return true
  }
}

function save(key: string, value: string): void {
  try {
    localStorage.setItem(key, value)
  } catch {
    /* storage unavailable */
  }
}

export type BookTab = 'portfolio' | 'swap' | 'orders' | 'history' | 'tools'

/** A sheet the BOOK asks the chat to open: Send posts into the chat, so it lives there. */
export type DeskSheet =
  'pick' | 'send' | 'multisend' | 'allowances' | 'inspect' | 'network' | 'burn' | null

interface TradingUiStore {
  bookWidth: number
  bookOpen: boolean
  bookTab: BookTab
  /** The full-width desk instead of chat + BOOK. */
  deskMode: boolean
  sheet: DeskSheet
  setBookWidth(width: number): void
  toggleBook(): void
  setBookOpen(open: boolean): void
  setBookTab(tab: BookTab): void
  setDeskMode(on: boolean): void
  openSheet(sheet: DeskSheet): void
}

export const useTradingUi = create<TradingUiStore>((set) => ({
  bookWidth: loadWidth(),
  bookOpen: loadOpen(),
  bookTab: 'portfolio',
  deskMode: false,
  sheet: null,
  setBookWidth(width) {
    const clamped = Math.round(Math.min(BOOK_MAX, Math.max(BOOK_MIN, width)))
    save(WIDTH_KEY, String(clamped))
    set({ bookWidth: clamped })
  },
  toggleBook() {
    set((s) => {
      save(OPEN_KEY, String(!s.bookOpen))
      return { bookOpen: !s.bookOpen }
    })
  },
  setBookOpen(open) {
    save(OPEN_KEY, String(open))
    set({ bookOpen: open })
  },
  setBookTab(tab) {
    set({ bookTab: tab, bookOpen: true })
    save(OPEN_KEY, 'true')
  },
  setDeskMode(on) {
    set({ deskMode: on })
  },
  openSheet(sheet) {
    set({ sheet })
  },
}))

/**
 * The desk's chat session, remembered across launches. A key from before the
 * desk had its own agent (`agent:main:…`) is not the desk's any more: it reads
 * as absent, so a fresh one is minted and the old chat stays in the sidebar.
 */
export function readTradingSessionKey(): string {
  try {
    const key = localStorage.getItem(SESSION_KEY) || ''
    return isTradingAgentKey(key) ? key : ''
  } catch {
    return ''
  }
}

export function writeTradingSessionKey(key: string): void {
  save(SESSION_KEY, key)
  save(FILED_KEY, 'false')
}

/** The desk's key, minted on first need so mode derivation can read it synchronously. */
export function ensureTradingSessionKey(mint: () => string): string {
  const existing = readTradingSessionKey()
  if (existing) return existing
  const fresh = mint()
  writeTradingSessionKey(fresh)
  return fresh
}

export function readReturnSession(): string | null {
  try {
    return sessionStorage.getItem(RETURN_KEY) || null
  } catch {
    return null
  }
}

export function writeReturnSession(key: string | null): void {
  try {
    if (key) sessionStorage.setItem(RETURN_KEY, key)
    else sessionStorage.removeItem(RETURN_KEY)
  } catch {
    /* storage unavailable */
  }
}

export function readTradingSessionFiled(): boolean {
  try {
    return localStorage.getItem(FILED_KEY) === 'true'
  } catch {
    return false
  }
}

export function writeTradingSessionFiled(filed: boolean): void {
  save(FILED_KEY, String(filed))
}

export function readTradingAgentVersion(): number {
  try {
    const raw = Number(localStorage.getItem(AGENT_VERSION_KEY))
    return Number.isFinite(raw) ? raw : 0
  } catch {
    return 0
  }
}

export function writeTradingAgentVersion(version: number): void {
  save(AGENT_VERSION_KEY, String(version))
}
