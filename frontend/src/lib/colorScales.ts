// Shared color ramps for the map layers and the Legend, so the two never
// drift apart. PM2.5's breakpoints follow the commonly used US EPA AQI
// bands (µg/m3); PDI's are a plain, unrelated sequential ramp — using a
// visually distinct scale (not the AQI colors) is deliberate, so a PDI
// layer is never mistaken for a second pollution measurement.
//
// Colors are tuned for readability against the dark monochrome basemap
// (see lib/mapTheme.ts). Low-end greens are kept saturated enough to
// stand out on near-black; high-end reds/purples remain vivid.

import type { ExpressionSpecification } from '@maplibre/maplibre-gl-style-spec'

export interface ColorStop {
  value: number
  color: string
  label: string
}

export const PM25_COLOR_SCALE: ColorStop[] = [
  { value: 0, color: '#22c55e', label: 'Good (0)' },
  { value: 12, color: '#eab308', label: 'Moderate (12)' },
  { value: 35, color: '#f97316', label: 'Unhealthy for sensitive groups (35)' },
  { value: 55, color: '#ef4444', label: 'Unhealthy (55)' },
  { value: 150, color: '#a855f7', label: 'Very unhealthy (150)' },
  { value: 250, color: '#7c3aed', label: 'Hazardous (250+)' },
]

export const PDI_COLOR_SCALE: ColorStop[] = [
  { value: -100, color: '#3b82f6', label: 'Strong sink (-100)' },
  { value: 0, color: '#525252', label: 'Neutral (0)' },
  { value: 50, color: '#f59e0b', label: 'Elevated pressure (50)' },
  { value: 100, color: '#dc2626', label: 'High pressure (100)' },
]

/** Color used for a cell whose value is null (no estimate yet) — distinct
 * from every ramp so "no data" is never confused with "measured zero". */
export const NO_DATA_COLOR = '#2a2e36'

/** Builds a MapLibre `interpolate` expression from a color scale, with a
 * `case` wrapper so a null `value` (no estimate for that cell) renders as
 * NO_DATA_COLOR instead of falling through to a runtime style error.
 *
 * Built dynamically from `scale` rather than authored as one literal
 * expression, so the map layers and the Legend are guaranteed to use
 * exactly the same stops. `as` is required because MapLibre's expression
 * type is a fixed-arity tuple union meant for hand-authored literals — it
 * can't express "N stops built from an array" structurally, even though
 * the runtime shape (['interpolate', ['linear'], ['get', prop], v, c, ...])
 * is exactly what the style spec expects.
 */
export function colorScaleExpression(
  scale: ColorStop[],
  property: string,
): ExpressionSpecification {
  const stops = scale.flatMap((stop) => [stop.value, stop.color])
  return [
    'case',
    ['==', ['get', property], null],
    NO_DATA_COLOR,
    ['interpolate', ['linear'], ['get', property], ...stops],
  ] as unknown as ExpressionSpecification
}

// ---------------------------------------------------------------------------
// Wind-speed ramp for the animated flow overlay (m/s). A cool blue -> cyan ->
// white ramp, deliberately distinct from the PM2.5 (green->red->purple) and
// PDI scales so the wind layer reads as its own variable rather than
// competing with the pollution colouring.
// ---------------------------------------------------------------------------

export const WIND_SPEED_SCALE: ColorStop[] = [
  { value: 0, color: '#64748b', label: 'Calm' },
  { value: 3, color: '#38bdf8', label: '3 m/s' },
  { value: 6, color: '#22d3ee', label: '6 m/s' },
  { value: 10, color: '#7dd3fc', label: '10 m/s' },
  { value: 15, color: '#e0f2fe', label: '15 m/s' },
  { value: 22, color: '#ffffff', label: '22+ m/s' },
]

function hexToRgb(hex: string): [number, number, number] {
  const n = parseInt(hex.slice(1), 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
}

/** Linearly interpolated color for a wind speed, as an rgb() string. */
export function windSpeedColor(speed: number): string {
  const scale = WIND_SPEED_SCALE
  if (speed <= scale[0].value) return scale[0].color
  for (let i = 1; i < scale.length; i++) {
    if (speed <= scale[i].value) {
      const a = scale[i - 1]
      const b = scale[i]
      const t = (speed - a.value) / (b.value - a.value)
      const [ar, ag, ab] = hexToRgb(a.color)
      const [br, bg, bb] = hexToRgb(b.color)
      const r = Math.round(ar + (br - ar) * t)
      const g = Math.round(ag + (bg - ag) * t)
      const bl = Math.round(ab + (bb - ab) * t)
      return `rgb(${r}, ${g}, ${bl})`
    }
  }
  return scale[scale.length - 1].color
}
