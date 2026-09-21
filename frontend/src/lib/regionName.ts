import { findStateForPoint } from './stateBoundaries'
import type { StateBoundaries } from './stateBoundaries'

// Human-readable region titles for the hex drawer. The demo/MVP has no
// land-use/poi backend yet, so the zone descriptor is derived
// deterministically from the cell's own coordinates — the same hex always
// reads as the same place. When a real gazetteer endpoint lands, swap this
// for that lookup; the title shape (State — Descriptive Zone) stays.

const ZONE_LABEL: string[] = [
  'Industrial Cluster',
  'Metro Corridor',
  'Agricultural Belt',
  'Logistics Hub',
  'Thermal Power Region',
  'Coastal Industry Zone',
]

/** Stable, cheap coordinate hash in [0, 1) — same cell, same zone. */
function coordHash(lat: number, lon: number): number {
  const x = Math.sin(lat * 127.1 + lon * 311.7) * 43758.5453
  return x - Math.floor(x)
}

/**
 * A readable region title for a hex: "Gujarat - Ahmedabad Industrial
 * Cluster" shaped output. Falls back to a coordinate label when the state
 * boundary dataset has no coverage for the point.
 */
export function regionTitle(lat: number, lon: number, boundaries: StateBoundaries | null): string {
  const state = boundaries ? findStateForPoint(lat, lon, boundaries) : null
  const zone = ZONE_LABEL[Math.floor(coordHash(lat, lon) * ZONE_LABEL.length)]
  if (state === null) {
    const label = `${zone} · ${lat.toFixed(2)}, ${lon.toFixed(2)}`
    return label
  }
  return `${state} - ${zone}`
}
