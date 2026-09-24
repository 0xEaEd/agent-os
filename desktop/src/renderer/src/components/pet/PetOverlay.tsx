import './pet.css'
import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { useRpc } from '@/app/providers'
import {
  PET_FRAME_H,
  PET_FRAME_W,
  PET_LOOP_MS,
  petSheetUrl,
  petStateRow,
  rowFrameCounts,
  sheetGeometry,
  type SheetGeometry,
} from '@shared/pet'
import { useSettings } from '~/stores/settings'
import { bindPetSignals, usePet } from '~/stores/pet'
import {
  anchorFromPixels,
  defaultAnchor,
  parseStoredAnchor,
  petYieldsAt,
  pixelsFromAnchor,
  pointInRect,
  type PetAnchor,
  type Size,
} from './logic'

const POS_KEY = 'agentos-desktop.petPosition'

interface Sheet extends SheetGeometry {
  /** Real frames per row (see rowFrameCounts). */
  frames: number[]
  /** The pixels could not be read; every row runs the full stride. */
  tainted?: boolean
}

function loadAnchor(size: Size, win: Size): PetAnchor | null {
  try {
    return parseStoredAnchor(JSON.parse(localStorage.getItem(POS_KEY) || 'null'), size, win)
  } catch {
    return null
  }
}

function saveAnchor(anchor: PetAnchor): void {
  try {
    localStorage.setItem(POS_KEY, JSON.stringify(anchor))
  } catch {
    /* storage unavailable */
  }
}

/** The window's inner size, re-read on every resize. */
function subscribeWindow(onChange: () => void): () => void {
  // The last resize event of a burst can fire before the metrics settle;
  // read once more on the next frame so the pet ends on the final size.
  let frame = 0
  const onResize = () => {
    onChange()
    cancelAnimationFrame(frame)
    frame = requestAnimationFrame(onChange)
  }
  window.addEventListener('resize', onResize)
  return () => {
    window.removeEventListener('resize', onResize)
    cancelAnimationFrame(frame)
  }
}
function readWindow(): string {
  return `${window.innerWidth}x${window.innerHeight}`
}
function useWindowSize(): Size {
  const key = useSyncExternalStore(subscribeWindow, readWindow)
  const [w, h] = key.split('x').map(Number)
  return { w: w ?? 0, h: h ?? 0 }
}

/** Alpha threshold at or below which a frame counts as transparent padding. */
const BLANK_ALPHA = 8
/** Sample every Nth pixel: enough to catch any sprite, cheap on an 11-row sheet. */
const SAMPLE_STRIDE = 4

/**
 * Decode the sheet once and measure which frames of each row are real.
 * Runs off the loaded <img> on a scratch canvas; a decode failure leaves
 * every row at the full stride, which is what the petdex web app does.
 */
function measureSheet(img: HTMLImageElement): Sheet | null {
  const geometry = sheetGeometry(img.naturalWidth, img.naturalHeight)
  if (!geometry) return null
  const full = Array.from({ length: geometry.rows }, () => 6)
  let tainted = false
  try {
    const canvas = document.createElement('canvas')
    canvas.width = img.naturalWidth
    canvas.height = img.naturalHeight
    const ctx = canvas.getContext('2d', { willReadFrequently: true })
    if (!ctx) return { ...geometry, frames: full }
    ctx.drawImage(img, 0, 0)
    const isBlank = (col: number, row: number) => {
      const data = ctx.getImageData(col * PET_FRAME_W, row * PET_FRAME_H, PET_FRAME_W, PET_FRAME_H)
      for (let y = 0; y < PET_FRAME_H; y += SAMPLE_STRIDE) {
        for (let x = 0; x < PET_FRAME_W; x += SAMPLE_STRIDE) {
          if (data.data[(y * PET_FRAME_W + x) * 4 + 3]! > BLANK_ALPHA) return false
        }
      }
      return true
    }
    return { ...geometry, frames: rowFrameCounts(geometry, isBlank) }
  } catch (err) {
    tainted = true
    console.warn('[pet] could not measure sheet frames', err)
    return { ...geometry, frames: full, tainted }
  }
}

/**
 * The petdex mascot floating over the window: one sheet, one row per state,
 * the row's real frames stepped by CSS the way the petdex web app animates.
 * Drag it anywhere (the spot is remembered); click it and it waves back.
 */
export function PetOverlay() {
  const rpc = useRpc()
  const pet = useSettings((s) => s.settings.pet)
  useEffect(() => bindPetSignals(rpc), [rpc])
  if (!pet.enabled || !pet.slug) return null
  return <PetSprite slug={pet.slug} scale={pet.scale} />
}

function PetSprite({ slug, scale }: { slug: string; scale: number }) {
  const state = usePet((s) => s.state)
  const poke = usePet((s) => s.poke)
  const [sheet, setSheet] = useState<Sheet | null>(null)
  const win = useWindowSize()
  // Whole pixels: a fractional frame width puts every step on a sub-pixel
  // boundary and the sprite shimmers.
  const w = Math.max(1, Math.round(PET_FRAME_W * scale))
  const h = Math.max(1, Math.round(PET_FRAME_H * scale))
  const size: Size = { w, h }
  // The spot is kept as a share of the free space, so a resized window
  // carries the pet along and can never strand it off screen.
  const [anchor, setAnchor] = useState<PetAnchor>(
    () => loadAnchor(size, win) ?? defaultAnchor(size, win),
  )
  const ref = useRef<HTMLButtonElement>(null)
  const url = petSheetUrl(slug)
  // The pointer effect binds once; these keep it reading current values
  // without re-binding a window listener on every animation frame of a drag.
  const anchorRef = useRef(anchor)
  const sizeRef = useRef(size)
  const winRef = useRef(win)
  const pokeRef = useRef(poke)

  // Measure the sheet once per pet: the grid says which rows exist, the
  // alpha says how many frames each row really has.
  useEffect(() => {
    let cancelled = false
    const img = new Image()
    // Needed to read pixels back in measureSheet; the pet scheme allows it.
    img.crossOrigin = 'anonymous'
    img.onload = () => {
      if (!cancelled) setSheet(measureSheet(img))
    }
    img.onerror = () => {
      if (!cancelled) setSheet(null)
    }
    img.src = url
    return () => {
      cancelled = true
    }
  }, [url])

  // Keep the window listener's view of the live values current. Ref writes
  // belong in an effect, not in render (react-hooks/refs).
  useEffect(() => {
    anchorRef.current = anchor
    sizeRef.current = size
    winRef.current = win
    pokeRef.current = poke
  })

  // Pointer handling lives on the window, not on the sprite, and the sprite
  // itself is `pointer-events: none` (pet.css). A mascot that takes pointer
  // events is a mascot that silently disables whatever it stands on — the
  // audit found it eating the composer's route button and a ledger card's
  // "Inspect tx"/"View transaction". Now the press is only ever the pet's when
  // no control claims that point AND nothing is stacked above the pet there.
  useEffect(() => {
    let live: { dx: number; dy: number; moved: boolean } | null = null
    let latest = anchorRef.current

    const onMove = (e: PointerEvent) => {
      if (!live) return
      live.moved = true
      latest = anchorFromPixels(
        { x: e.clientX - live.dx, y: e.clientY - live.dy },
        sizeRef.current,
        winRef.current,
      )
      setAnchor(latest)
    }
    const onUp = () => {
      const d = live
      live = null
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onUp)
      if (!d) return
      if (d.moved) saveAnchor(latest)
      else pokeRef.current()
    }
    const onDown = (e: PointerEvent) => {
      // Read the node here, not when the listener binds: the sprite only
      // mounts once the sheet has decoded, which is after this effect runs.
      const el = ref.current
      if (!el || e.button !== 0 || live) return
      // A real control under the pointer always wins. `e.target` is already the
      // element BELOW the pet, because the pet does not take pointer events.
      if (petYieldsAt(e.target as Element | null)) return
      const rect = el.getBoundingClientRect()
      if (!pointInRect(rect, e.clientX, e.clientY)) return
      // Topmost test: a sheet or menu drawn over the pet must keep the press.
      el.style.pointerEvents = 'auto'
      const top = document.elementFromPoint(e.clientX, e.clientY)
      el.style.pointerEvents = ''
      if (top !== el) return
      e.preventDefault()
      e.stopPropagation()
      live = { dx: e.clientX - rect.left, dy: e.clientY - rect.top, moved: false }
      latest = anchorRef.current
      window.addEventListener('pointermove', onMove)
      window.addEventListener('pointerup', onUp)
      window.addEventListener('pointercancel', onUp)
    }
    window.addEventListener('pointerdown', onDown, true)
    return () => {
      window.removeEventListener('pointerdown', onDown, true)
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onUp)
    }
  }, [])

  if (!sheet) return null
  const pos = pixelsFromAnchor(anchor, size, win)
  const row = petStateRow(state, sheet.rows)
  const frames = sheet.frames[row] ?? 1
  const style: React.CSSProperties = {
    width: w,
    height: h,
    backgroundImage: `url("${url}")`,
    backgroundSize: `${sheet.cols * w}px ${sheet.rows * h}px`,
    backgroundPositionY: `${-row * h}px`,
    ['--pet-frame-w' as string]: `${w}px`,
    ['--pet-frames' as string]: String(frames),
    ['--pet-loop' as string]: `${Math.round((PET_LOOP_MS * frames) / 6)}ms`,
    left: pos.x,
    top: pos.y,
  }

  return (
    <button
      // A new row restarts the walk from its first frame, as Hermes does.
      key={row}
      ref={ref}
      type="button"
      className="pet app-no-drag"
      data-state={state}
      data-frames={frames}
      data-measured={sheet.tainted ? 'false' : 'true'}
      aria-label={`Pet: ${state}`}
      title={slug}
      style={style}
      // Keyboard and assistive tech still reach it as an ordinary button; the
      // pointer path is the window listener above, which cannot swallow a click.
      onClick={() => poke()}
    />
  )
}
