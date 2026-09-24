/**
 * Petdex pets, the format Hermes and Codex share: a `pet.json` beside one
 * spritesheet, a grid of 192×208 frames, one row per animation state, six
 * frames stepped over 1.1s. See https://github.com/crafter-station/petdex.
 */

export const PET_FRAME_W = 192
export const PET_FRAME_H = 208
export const PET_FRAMES_PER_STATE = 6
export const PET_LOOP_MS = 1100

export const PET_MIN_SCALE = 0.1
export const PET_MAX_SCALE = 3
export const PET_DEFAULT_SCALE = 0.33

export function clampPetScale(scale: number): number {
  if (!Number.isFinite(scale)) return PET_DEFAULT_SCALE
  return Math.min(PET_MAX_SCALE, Math.max(PET_MIN_SCALE, scale))
}

/** A slug is a petdex id: what the manifest calls it and the folder it lives in. */
export function isPetSlug(value: unknown): value is string {
  return typeof value === 'string' && /^[a-z0-9][a-z0-9-]{0,80}$/.test(value)
}

/** What the pet is doing on screen. Hermes' activity names. */
export type PetState = 'idle' | 'wave' | 'run' | 'failed' | 'review' | 'jump' | 'waiting'

/** Current petdex sheets: 8 columns × 9 rows (v1) or × 11 rows (v2, two custom rows). */
const CODEX_ROWS = [
  'idle',
  'running-right',
  'running-left',
  'waving',
  'jumping',
  'failed',
  'waiting',
  'running',
  'review',
] as const

/** Older 9 × 8 sheets. */
const LEGACY_ROWS = ['idle', 'wave', 'run', 'failed', 'review', 'jump', 'extra1', 'extra2'] as const

const ALIASES: Record<PetState, readonly string[]> = {
  idle: ['idle'],
  wave: ['wave', 'waving'],
  jump: ['jump', 'jumping'],
  run: ['run', 'running'],
  failed: ['failed'],
  review: ['review'],
  waiting: ['waiting'],
}

export interface SheetGeometry {
  cols: number
  rows: number
}

/** Frame grid from the sheet's pixel size; a sheet that is not a whole grid is rejected. */
export function sheetGeometry(width: number, height: number): SheetGeometry | null {
  const cols = width / PET_FRAME_W
  const rows = height / PET_FRAME_H
  if (!Number.isInteger(cols) || !Number.isInteger(rows) || cols < 1 || rows < 1) return null
  return { cols, rows }
}

/**
 * Real frames per row. Petdex sheets are left-packed: a state with fewer
 * frames than the grid is wide leaves the rest of its row transparent, so
 * animating across the whole row blinks the pet out. Stop at the first
 * blank frame, and never step more than PET_FRAMES_PER_STATE.
 */
export function rowFrameCounts(
  geometry: SheetGeometry,
  isBlank: (col: number, row: number) => boolean,
): number[] {
  const counts: number[] = []
  for (let row = 0; row < geometry.rows; row++) {
    let n = 0
    while (n < Math.min(geometry.cols, PET_FRAMES_PER_STATE) && !isBlank(n, row)) n++
    counts.push(Math.max(1, n))
  }
  return counts
}

/** The sheet row that animates `state`; idle when the sheet has no such row. */
export function petStateRow(state: PetState, rows: number): number {
  const taxonomy: readonly string[] = rows >= CODEX_ROWS.length ? CODEX_ROWS : LEGACY_ROWS
  for (const name of ALIASES[state]) {
    const i = taxonomy.indexOf(name)
    if (i >= 0 && i < rows) return i
  }
  return 0
}

export interface PetSignals {
  /** A tool or the turn just failed. */
  error?: boolean
  /** An explicit success beat (a plan finished). */
  celebrate?: boolean
  /** The turn finished cleanly a moment ago. */
  justCompleted?: boolean
  /** Blocked on the user: an approval or a question is open. */
  awaitingInput?: boolean
  toolRunning?: boolean
  reasoning?: boolean
  busy?: boolean
}

/**
 * One row shows at a time, so the most salient signal wins. Same priority
 * as Hermes' `derive_pet_state`, so a pet behaves the same in both apps.
 */
export function derivePetState(s: PetSignals): PetState {
  if (s.error) return 'failed'
  if (s.celebrate) return 'jump'
  if (s.justCompleted) return 'wave'
  if (s.awaitingInput) return 'waiting'
  if (s.toolRunning) return 'run'
  if (s.reasoning) return 'review'
  if (s.busy) return 'run'
  return 'idle'
}

/** A row of the public manifest (petdex.dev/api/manifest). */
export interface PetManifestEntry {
  slug: string
  displayName: string
  kind: string
  submittedBy: string
  spriteVersion: number
}

/** A pet on disk in the app's pets directory. */
export interface InstalledPet {
  slug: string
  displayName: string
  description: string
  /** Where the renderer loads the sheet from (`agentos-pet://sheet/<slug>`). */
  sheetUrl: string
}

/** What a pet folder on disk must say about itself before it is copied in. */
export interface PetFolderMeta {
  slug: string
  displayName: string
  description: string
  /** The sheet's file name inside the folder (`spritesheetPath`, default `spritesheet.webp`). */
  sheetFile: string
}

export type PetFolderCheck = { ok: true; pet: PetFolderMeta } | { ok: false; reason: string }

/**
 * Read a `pet.json` someone handed us (a download, a friend's export) and
 * decide whether it is a petdex pet. The sheet's pixel size is checked
 * here too, so a folder is refused before anything is copied.
 */
export function parsePetFolder(
  manifest: unknown,
  sheet: { width: number; height: number } | null,
  fallbackSlug = '',
): PetFolderCheck {
  const meta = manifest && typeof manifest === 'object' ? (manifest as Record<string, unknown>) : {}
  const rawSlug = typeof meta.id === 'string' && meta.id.trim() ? meta.id.trim() : fallbackSlug
  const slug = rawSlug.toLowerCase()
  if (!isPetSlug(slug)) {
    return {
      ok: false,
      reason: `"${rawSlug || '?'}" is not a pet id (lowercase letters, digits, dashes).`,
    }
  }
  const sheetFile =
    typeof meta.spritesheetPath === 'string' && meta.spritesheetPath.trim()
      ? meta.spritesheetPath.trim()
      : 'spritesheet.webp'
  if (sheetFile.includes('/') || sheetFile.includes('\\') || !sheetFile.endsWith('.webp')) {
    return { ok: false, reason: `spritesheetPath must name a .webp file in the same folder.` }
  }
  if (!sheet) return { ok: false, reason: `${sheetFile} is missing or not an image.` }
  if (!sheetGeometry(sheet.width, sheet.height)) {
    return {
      ok: false,
      reason: `${sheetFile} is ${sheet.width}×${sheet.height}; a petdex sheet is a grid of ${PET_FRAME_W}×${PET_FRAME_H} frames.`,
    }
  }
  return {
    ok: true,
    pet: {
      slug,
      displayName: String(meta.displayName || slug),
      description: String(meta.description || ''),
      sheetFile,
    },
  }
}

/**
 * A WebP's pixel size, read straight from its header. Electron's
 * `nativeImage` decodes PNG and JPEG only — it returns an empty image for
 * every WebP — and petdex sheets are all WebP, so the size has to come from
 * the bytes. Covers the three stream flavours: VP8 (lossy), VP8L (lossless)
 * and VP8X (extended, the canvas size).
 */
export function webpSize(bytes: Uint8Array): { width: number; height: number } | null {
  const ascii = (at: number, n: number) => String.fromCharCode(...bytes.subarray(at, at + n))
  if (bytes.length < 30 || ascii(0, 4) !== 'RIFF' || ascii(8, 4) !== 'WEBP') return null
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
  const fourcc = ascii(12, 4)
  const payload = 20 // 12 (RIFF header) + 4 (fourcc) + 4 (chunk size)
  if (fourcc === 'VP8X') {
    const width = (view.getUint32(payload + 4, true) & 0xffffff) + 1
    const height = (view.getUint32(payload + 6, true) >>> 8) + 1
    return { width, height }
  }
  if (fourcc === 'VP8L') {
    if (bytes[payload] !== 0x2f) return null
    const bits = view.getUint32(payload + 1, true)
    return { width: (bits & 0x3fff) + 1, height: ((bits >>> 14) & 0x3fff) + 1 }
  }
  if (fourcc === 'VP8 ') {
    // 3-byte frame tag, then the start code 0x9d 0x01 0x2a.
    if (bytes[payload + 3] !== 0x9d || bytes[payload + 4] !== 0x01 || bytes[payload + 5] !== 0x2a) {
      return null
    }
    return {
      width: view.getUint16(payload + 6, true) & 0x3fff,
      height: view.getUint16(payload + 8, true) & 0x3fff,
    }
  }
  return null
}

export const PET_SCHEME = 'agentos-pet'

export function petSheetUrl(slug: string): string {
  return `${PET_SCHEME}://sheet/${slug}`
}
