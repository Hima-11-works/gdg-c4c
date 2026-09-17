// Centralized dark-mode map theme. Every visual constant that a future
// theme swap might touch lives here — basemap palette, overlay palette,
// layer paint properties, wind-arrow styling. MapView imports these;
// nothing else in the app should hardcode map colors.

import type { StyleSpecification } from '@maplibre/maplibre-gl-style-spec'

// ---------------------------------------------------------------------------
// Basemap palette — monochrome dark, never competes with pollution overlays.
// ---------------------------------------------------------------------------

export const BASEMAP = {
  background: '#0e1117',     // near-black — ocean / world fill
  land: '#1a1d23',           // dark charcoal — all land masses
  india: '#22262e',          // slightly lighter dark gray — India territory
  stateBorder: '#3a3f4a',    // subtle medium-gray — state/UT boundaries
  intlBorder: '#4a5060',     // slightly stronger — international boundaries
  label: '#9ca3af',          // light gray — country/city labels
  labelHalo: '#0e1117',      // matches background — label readability halo
  coastline: '#2a2e36',      // subdued — coastline line
} as const

// ---------------------------------------------------------------------------
// Data overlay palette — pollution is the primary colored layer.
// ---------------------------------------------------------------------------

export const OVERLAY = {
  indiaFill: '#22262e',      // matches BASEMAP.india — fills India territory
  indiaBorder: '#4a5060',    // solid outer border of India
  noData: '#2a2e36',         // dark — cells with no estimate
  cellOutline: '#00000030',  // faint — hex cell borders
  maskFill: '#0e1117',       // covers everything outside the buffered border
} as const

// ---------------------------------------------------------------------------
// Wind arrow — subdued neutral, never dominant.
// ---------------------------------------------------------------------------

export const WIND = {
  arrowColor: '#9ca3af',     // light gray — visible but not prominent
  arrowStroke: '#0e1117',    // near-black outline for contrast
} as const

// ---------------------------------------------------------------------------
// Style URL — the demotiles style is fetched, then patched to a dark
// monochrome palette by patchBasemapStyle(). We load the real style JSON
// (with its proven vector source + glyph config) and rewrite its colors
// rather than authoring an inline style object, because MapLibre's runtime
// can reject a hand-built StyleSpecification that's subtly incomplete.
// ---------------------------------------------------------------------------

export const BASE_STYLE_URL = 'https://demotiles.maplibre.org/style.json'

/** Takes a fetched demotiles StyleSpecification and rewrites every layer's
 *  paint/layout to a dark monochrome palette. Returns the mutated object
 *  (same reference — mutates in place). */
export function patchBasemapStyle(style: StyleSpecification): StyleSpecification {
  for (const layer of style.layers ?? []) {
    const id = layer.id
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const layout = layer.layout as Record<string, any> | undefined

    if (id === 'background') {
      layer.paint = { 'background-color': BASEMAP.background } as never
    }

    if (id === 'countries-fill') {
      layer.paint = { 'fill-color': BASEMAP.land } as never
    }

    if (id === 'coastline') {
      layer.paint = {
        'line-color': BASEMAP.coastline,
        'line-width': ['interpolate', ['linear'], ['zoom'], 0, 1, 6, 3, 14, 6],
        'line-blur': 0.5,
      } as never
      if (layout) {
        layout['line-cap'] = 'round'
        layout['line-join'] = 'round'
      }
    }

    if (id === 'countries-boundary') {
      layer.paint = {
        'line-color': BASEMAP.intlBorder,
        'line-width': ['interpolate', ['linear'], ['zoom'], 1, 0.5, 6, 1.5, 14, 4],
        'line-opacity': ['interpolate', ['linear'], ['zoom'], 3, 0.3, 6, 0.8],
      } as never
      if (layout) {
        layout['line-cap'] = 'round'
        layout['line-join'] = 'round'
      }
    }

    if (id === 'countries-label') {
      layer.minzoom = 2
      layer.paint = {
        'text-color': BASEMAP.label,
        'text-halo-color': BASEMAP.labelHalo,
        'text-halo-width': 1.2,
        'text-halo-blur': 0.5,
      } as never
    }

    // Remove the crimea overlay fill — it would show as a bright purple
    // patch on the dark map.
    if (id === 'crimea-fill' && layout) {
      layout.visibility = 'none'
    }
  }

  return style
}
