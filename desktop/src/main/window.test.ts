// @vitest-environment node
import { describe, expect, it, vi } from 'vitest'

// The module reaches for BrowserWindow at call time only; the helpers under
// test are pure, so electron itself is never needed here.
vi.mock('electron', () => ({ BrowserWindow: class {}, nativeTheme: {}, shell: {} }))
vi.mock('@electron-toolkit/utils', () => ({ is: { dev: false } }))

import { appOriginFor, isAllowedExternalUrl, isAppNavigation } from './window'

describe('isAllowedExternalUrl', () => {
  it('lets web and mail links out to the OS', () => {
    expect(isAllowedExternalUrl('https://basescan.org/tx/0xabc')).toBe(true)
    expect(isAllowedExternalUrl('http://localhost:8080/')).toBe(true)
    expect(isAllowedExternalUrl('mailto:someone@example.com')).toBe(true)
  })

  it('refuses everything that is not a web or mail link', () => {
    // A rendered link is content the agent may have written.
    for (const url of [
      'javascript:alert(1)',
      'file:///etc/passwd',
      'file:///var/app-support/agentos/vault.json',
      'data:text/html,<script>alert(1)</script>',
      'agentos-qr:0xabc',
      'ftp://example.com/x',
      'smb://nas/share',
      'not a url',
      '',
    ]) {
      expect(isAllowedExternalUrl(url), url).toBe(false)
    }
  })
})

describe('appOriginFor', () => {
  it('is the dev server origin when a renderer URL is set', () => {
    expect(appOriginFor('http://localhost:5173/index.html?x=1', '/app/out/renderer')).toBe(
      'http://localhost:5173',
    )
  })

  it('is the file URL of the renderer directory otherwise', () => {
    expect(appOriginFor(undefined, '/app/out/renderer')).toBe('file:///app/out/renderer/')
    expect(appOriginFor('', '/app/out/renderer')).toBe('file:///app/out/renderer/')
    // An unparsable renderer URL falls back to the packaged renderer.
    expect(appOriginFor('nonsense', '/app/out/renderer')).toBe('file:///app/out/renderer/')
  })
})

describe('isAppNavigation', () => {
  const dev = 'http://localhost:5173'
  const packaged = 'file:///app/out/renderer/'

  it('allows the app to move within its own dev origin', () => {
    expect(isAppNavigation('http://localhost:5173/', dev)).toBe(true)
    expect(isAppNavigation('http://localhost:5173/index.html#/trading', dev)).toBe(true)
  })

  it('stops a dev renderer leaving its origin', () => {
    for (const url of [
      'http://localhost:5174/',
      'https://localhost:5173/',
      'http://evil.example/',
      'https://basescan.org/tx/0xabc',
      'javascript:alert(1)',
      'data:text/html,hi',
      'file:///app/out/renderer/index.html',
      'file:///etc/passwd',
      'garbage',
    ]) {
      expect(isAppNavigation(url, dev), url).toBe(false)
    }
  })

  it('allows a packaged renderer to move within its own directory only', () => {
    expect(isAppNavigation('file:///app/out/renderer/index.html', packaged)).toBe(true)
    expect(isAppNavigation('file:///app/out/renderer/index.html#/trading', packaged)).toBe(true)
    for (const url of [
      'file:///app/out/other.html',
      'file:///app/out/renderer-evil/index.html',
      'file:///etc/passwd',
      'http://localhost:5173/',
      'https://evil.example/',
      'javascript:alert(1)',
      'data:text/html,hi',
    ]) {
      expect(isAppNavigation(url, packaged), url).toBe(false)
    }
  })
})
