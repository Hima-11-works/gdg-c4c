// Shared color ramps for the map layers and the Legend, so the two never
// drift apart. PM2.5's bands follow the Indian CPCB National Air Quality
// Index (NAQI) scale — Good (0–30), Satisfactory (31–60), Moderate
// (61–90), Poor (91–120), Very Poor (121–250), Severe (>250 µg/m³).
// PDI's are a plain, unrelated sequential ramp — using a visually
// distinct scale (not the pollution colors) is deliberate, so a PDI layer
// is never mistaken for a second pollution measurement.
//
// Colors are tuned for readability against the dark monochrome basemap
// (see lib/mapTheme.ts). The ramp steps green → light green → yellow →
// orange → red → maroon per CPCB.
//
// This single array drives everything color-related: the hex fill
// expression, the smooth view's `sampleColor`, contrast mode's band
// boundaries (`bandIndex`), and the Legend.

import type { ExpressionSpecification } from '@maplibre/maplibre-gl-style-spec'

export interface ColorStop {
  value: number
  color: string
  label: string
}

/** CPCB NAQI PM2.5 bands. `value` is the *lower* bound of each band —
 *  MapLibre's `interpolate` colors a value v with the last stop ≤ v, so
 *  a value of 31 picks Satisfactory, 91 picks Poor, and a value above
 *  the final stop clamps to Severe's maroon. */
export const PM25_COLOR_SCALE: ColorStop[] = [
  { value: 0, color: '#22c55e', label: 'Good (0–30)' },
  { value: 31, color: '#9ade51', label: 'Satisfactory (31–60)' },
  { value: 61, color: '#eab308', label: 'Moderate (61–90)' },
  { value: 91, color: '#f97316', label: 'Poor (91–120)' },
  { value: 121, color: '#ef4444', label: 'Very Poor (121–250)' },
  { value: 251, color: '#7f1d1d', label: 'Severe (250+)' },
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
