// Animated wind-flow overlay, in the style of weather-app "wind flow" maps:
// thousands of particles are advected through the wind field and drawn as
// short, fading, speed-coloured trails on a transparent canvas above the map.
//
// Particles live in screen space (that's what makes trails visible at any
// zoom — true geographic advection is sub-pixel at map scale). The wind field
// is sampled by unprojecting a coarse screen grid once per map move, then each
// particle bilinearly interpolates that grid per frame, so the hot loop does
// no projection work.
//
// Trails fade with `destination-out`, which lowers the alpha of already-drawn
// pixels without painting over the map beneath the canvas.

import type { Map as MapLibreMap } from 'maplibre-gl'
import { windSpeedColor } from './colorScales'
import { WindField } from './windField'
import type { WeatherReadingOut } from './types'

/** Screen speed for a given wind speed: 3 m/s -> 42 px/s. Wind is
 *  exaggerated for readability — true advection is sub-pixel at map scale. */
const PX_PER_MS_PER_S = 14
/** Particle lifetime before it is respawned (seconds). */
const MAX_AGE_S = 3.2
/** Alpha removed from existing trails per frame (higher = shorter trails). */
const TRAIL_FADE = 0.035
/** Screen-grid spacing for sampling the field (px). */
const GRID_STEP_PX = 30
/** Below this, particles are respawned rather than moved. */
const CALM_MS = 0.35
const MIN_PARTICLES = 700
const MAX_PARTICLES = 2600

interface ScreenGrid {
  cols: number
  rows: number
  step: number
  u: Float32Array
  v: Float32Array
  has: Uint8Array
}

export class WindFlowLayer {
  private readonly canvas: HTMLCanvasElement
  private readonly ctx: CanvasRenderingContext2D
  private readonly map: MapLibreMap
  private field: WindField | null = null
  private particles = new Float32Array(0)
  private count = 0
  private grid: ScreenGrid | null = null
  private dirty = true
  private raf = 0
  private last = 0
  private width = 0
  private height = 0
  private running = false

  private readonly onMove = (): void => {
    this.dirty = true
  }
  private readonly onMoveStart = (): void => {
    this.clear()
  }

  constructor(canvas: HTMLCanvasElement, map: MapLibreMap) {
    this.canvas = canvas
    const context = canvas.getContext('2d')
    if (!context) throw new Error('2D canvas context unavailable')
    this.ctx = context
    this.map = map
    map.on('move', this.onMove)
    map.on('movestart', this.onMoveStart)
    this.resize()
  }

  setData(readings: WeatherReadingOut[]): void {
    this.field = readings.length > 0 ? new WindField(readings) : null
    this.dirty = true
  }

  start(): void {
    if (this.running) return
    this.running = true
    this.last = performance.now()
    this.raf = requestAnimationFrame(this.tick)
  }

  stop(): void {
    this.running = false
    cancelAnimationFrame(this.raf)
  }

  destroy(): void {
    this.stop()
    this.map.off('move', this.onMove)
    this.map.off('movestart', this.onMoveStart)
    this.clear()
  }

  private resize(): void {
    const dpr = window.devicePixelRatio || 1
    const width = this.canvas.clientWidth || 1
    const height = this.canvas.clientHeight || 1
    this.width = width
    this.height = height
    this.canvas.width = Math.round(width * dpr)
    this.canvas.height = Math.round(height * dpr)
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    this.count = Math.max(
      MIN_PARTICLES,
      Math.min(MAX_PARTICLES, Math.round((width * height) / 1100)),
    )
    this.particles = new Float32Array(this.count * 3)
    for (let i = 0; i < this.count; i++) this.respawn(i)
    this.clear()
    this.dirty = true
  }

  private respawn(index: number): void {
    const p = index * 3
    this.particles[p] = Math.random() * this.width
    this.particles[p + 1] = Math.random() * this.height
    this.particles[p + 2] = Math.random() * MAX_AGE_S
  }

  private clear(): void {
    this.ctx.clearRect(0, 0, this.width, this.height)
  }

  private buildGrid(): void {
    const step = GRID_STEP_PX
    const cols = Math.ceil(this.width / step) + 1
    const rows = Math.ceil(this.height / step) + 1
    const u = new Float32Array(cols * rows)
    const v = new Float32Array(cols * rows)
    const has = new Uint8Array(cols * rows)
    if (this.field) {
      for (let r = 0; r < rows; r++) {
        for (let c = 0; c < cols; c++) {
          const { lat, lng } = this.map.unproject([c * step, r * step])
          const wind = this.field.sample(lat, lng)
          if (!wind) continue
          const idx = r * cols + c
          u[idx] = wind.u * PX_PER_MS_PER_S
          v[idx] = -wind.v * PX_PER_MS_PER_S // screen y grows downward
          has[idx] = 1
        }
      }
    }
    this.grid = { cols, rows, step, u, v, has }
    this.dirty = false
  }

  private readonly tick = (now: number): void => {
    if (!this.running) return
    const dt = Math.min(0.05, Math.max(0, (now - this.last) / 1000))
    this.last = now

    if (this.canvas.clientWidth !== this.width || this.canvas.clientHeight !== this.height) {
      this.resize()
    }
    if (this.dirty) this.buildGrid()

    const ctx = this.ctx
    ctx.globalCompositeOperation = 'destination-out'
    ctx.fillStyle = `rgba(0, 0, 0, ${TRAIL_FADE})`
    ctx.fillRect(0, 0, this.width, this.height)
    ctx.globalCompositeOperation = 'source-over'
    ctx.lineWidth = 1.15
    ctx.lineCap = 'round'

    const grid = this.grid
    const p = this.particles
    const width = this.width
    const height = this.height

    for (let i = 0; i < this.count; i++) {
      const pi = i * 3
      const x = p[pi]
      const y = p[pi + 1]
      const age = p[pi + 2]

      let vx = 0
      let vy = 0
      let ok = false
      if (grid) {
        const { cols, rows, step, u, v, has } = grid
        const c = Math.floor(x / step)
        const r = Math.floor(y / step)
        if (c >= 0 && r >= 0 && c < cols - 1 && r < rows - 1) {
          const fx = x / step - c
          const fy = y / step - r
          const i00 = r * cols + c
          const i10 = i00 + 1
          const i01 = i00 + cols
          const i11 = i01 + 1
          if (has[i00] && has[i10] && has[i01] && has[i11]) {
            const w00 = (1 - fx) * (1 - fy)
            const w10 = fx * (1 - fy)
            const w01 = (1 - fx) * fy
            const w11 = fx * fy
            vx = u[i00] * w00 + u[i10] * w10 + u[i01] * w01 + u[i11] * w11
            vy = v[i00] * w00 + v[i10] * w10 + v[i01] * w01 + v[i11] * w11
            ok = true
          }
        }
      }

      const speed = Math.hypot(vx, vy) / PX_PER_MS_PER_S
      if (!ok || speed < CALM_MS) {
        this.respawn(i)
        continue
      }

      const nx = x + vx * dt
      const ny = y + vy * dt
      if (nx < -24 || nx > width + 24 || ny < -24 || ny > height + 24 || age > MAX_AGE_S) {
        this.respawn(i)
        continue
      }

      ctx.strokeStyle = windSpeedColor(speed)
      ctx.beginPath()
      ctx.moveTo(x, y)
      ctx.lineTo(nx, ny)
      ctx.stroke()

      p[pi] = nx
      p[pi + 1] = ny
      p[pi + 2] = age + dt
    }

    this.raf = requestAnimationFrame(this.tick)
  }
}
