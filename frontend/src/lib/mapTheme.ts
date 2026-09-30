// Centralized map palettes. Pollution severity colors are shared between themes.

import type { Map as MapLibreMap } from 'maplibre-gl'
import type { StyleSpecification } from '@maplibre/maplibre-gl-style-spec'

export type MapTheme = 'light' | 'dark'

export const BASEMAP_THEMES = {
  light: {
    background: '#dce8ef',
    land: '#f1f3f4',
    india: '#e8f0fe',
    stateBorder: '#c7d2d9',
    intlBorder: '#aebdc7',
    label: '#5f6368',
    labelHalo: '#f8f9fa',
    coastline: '#c4d2da',
  },
  dark: {
    background: '#202124',
    land: '#303134',
    india: '#292f38',
    stateBorder: '#5f6368',
    intlBorder: '#80868b',
    label: '#bdc1c6',
    labelHalo: '#202124',
    coastline: '#3c4043',
  },
} as const

export const OVERLAY_THEMES = {
  light: {
    indiaFill: BASEMAP_THEMES.light.india,
    indiaBorder: '#9bb9e8',
    noData: '#e8eaed',
    cellOutline: '#5f636822',
  },
  dark: {
    indiaFill: BASEMAP_THEMES.dark.india,
    indiaBorder: '#8ab4f8',
    noData: '#485260',
    cellOutline: '#ffffff26',
  },
} as const

// Existing named exports remain the light defaults for consumers that only
// need a static legend or default paint value.
export const BASEMAP = BASEMAP_THEMES.light
export const OVERLAY = OVERLAY_THEMES.light

export const WIND = {
  arrowColor: '#9ca3af',
  arrowStroke: '#f8f9fa',
  pulse: '#1a73e8',
} as const

export const BASE_STYLE_URL = 'https://demotiles.maplibre.org/style.json'

/** Applies the selected palette to the fetched basemap before MapLibre starts. */
export function patchBasemapStyle(
  style: StyleSpecification,
  theme: MapTheme = 'light',
): StyleSpecification {
  const palette = BASEMAP_THEMES[theme]
  for (const layer of style.layers ?? []) {
    const layout = layer.layout as Record<string, unknown> | undefined

    if (layer.id === 'background') {
      layer.paint = { 'background-color': palette.background } as never
    }
    if (layer.id === 'countries-fill') {
      layer.paint = { 'fill-color': palette.land } as never
    }
    if (layer.id === 'coastline') {
      layer.paint = {
        'line-color': palette.coastline,
        'line-width': ['interpolate', ['linear'], ['zoom'], 0, 1, 6, 3, 14, 6],
        'line-blur': 0.5,
      } as never
      if (layout) { layout['line-cap'] = 'round'; layout['line-join'] = 'round' }
    }
    if (layer.id === 'countries-boundary') {
      layer.paint = {
        'line-color': palette.intlBorder,
        'line-width': ['interpolate', ['linear'], ['zoom'], 1, 0.5, 6, 1.5, 14, 4],
        'line-opacity': ['interpolate', ['linear'], ['zoom'], 3, 0.3, 6, 0.8],
      } as never
      if (layout) { layout['line-cap'] = 'round'; layout['line-join'] = 'round' }
    }
    if (layer.id === 'countries-label') {
      layer.minzoom = 2
      layer.paint = {
        'text-color': palette.label,
        'text-halo-color': palette.labelHalo,
        'text-halo-width': 1.2,
        'text-halo-blur': 0.5,
      } as never
    }
    if (layer.id === 'crimea-fill' && layout) layout.visibility = 'none'
  }
  return style
}

/** Recolors existing style layers in place; overlays, sources and camera stay intact. */
export function applyBasemapTheme(map: MapLibreMap, theme: MapTheme): void {
  const palette = BASEMAP_THEMES[theme]
  const overlay = OVERLAY_THEMES[theme]

  for (const layer of map.getStyle().layers ?? []) {
    if (layer.id === 'background') map.setPaintProperty(layer.id, 'background-color', palette.background)
    if (layer.id === 'countries-fill') map.setPaintProperty(layer.id, 'fill-color', palette.land)
    if (layer.id === 'coastline') map.setPaintProperty(layer.id, 'line-color', palette.coastline)
    if (layer.id === 'countries-boundary') map.setPaintProperty(layer.id, 'line-color', palette.intlBorder)
    if (layer.id === 'countries-label') {
      map.setPaintProperty(layer.id, 'text-color', palette.label)
      map.setPaintProperty(layer.id, 'text-halo-color', palette.labelHalo)
    }
  }

  const setLayerColor = (id: string, property: 'fill-color' | 'line-color', color: string) => {
    if (map.getLayer(id)) map.setPaintProperty(id, property, color)
  }
  setLayerColor('state-boundaries-line', 'line-color', palette.stateBorder)
  setLayerColor('india-outline-fill', 'fill-color', overlay.indiaFill)
  setLayerColor('india-outline-line', 'line-color', overlay.indiaBorder)
}
