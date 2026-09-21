// Major economic freight corridors — Western DFC / DMIC alignment — as
// hand-authored GeoJSON for an institutional-knowledge overlay. These are
// simplified illustrative geometries standing in for the real corridor
// network until a routes endpoint exists; the feature shape matches what
// such an endpoint should return.
//
// Overlays like this are contextual information for the AI source
// classifier and intervention planning — never a relocation recommendation.

import type { FeatureCollection, LineString, Point } from 'geojson'

export const FREIGHT_LINE_COLOR = '#00F5D4'

export const FREIGHT_ROUTES: FeatureCollection<LineString, { name: string }> = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      properties: { name: 'Western DFC (Dadri – JNPT)' },
      geometry: {
        type: 'LineString',
        coordinates: [
          [77.55, 28.55], // Dadri
          [76.94, 28.35], // Manesar
          [76.18, 27.9], // Rewari
          [76.35, 26.86], // Jaipur outer ring
          [74.87, 26.45], // Ajmer
          [74.25, 24.6], // Udaipur
          [73.0, 24.0], // Palanpur
          [72.43, 23.22], // Mehsana
          [72.58, 23.02], // Ahmedabad
          [73.0, 22.32], // Vadodara
          [72.85, 21.17], // Surat
          [72.85, 20.05], // Vapi
          [72.95, 19.0], // JNPT Mumbai
        ],
      },
    },
    {
      type: 'Feature',
      properties: { name: 'DMIC Investment Spine (Delhi – Ahmedabad)' },
      geometry: {
        type: 'LineString',
        coordinates: [
          [77.05, 28.61], // Delhi
          [76.94, 28.35], // Manesar
          [76.6, 27.9], // Bhiwadi / Khairthal
          [75.79, 26.92], // Jaipur
          [74.87, 26.45], // Kishangarh
          [73.7, 25.6], // Rajsamand
          [73.69, 24.58], // Udaipur
          [72.9, 23.8],
          [72.58, 23.02], // Ahmedabad
        ],
      },
    },
    {
      type: 'Feature',
      properties: { name: 'Eastern DFC feeder (Ludhiana – Dadri)' },
      geometry: {
        type: 'LineString',
        coordinates: [
          [75.85, 30.9], // Ludhiana
          [76.4, 30.2], // Patiala? approx corridor SW of Ambala
          [77.0, 29.35], // Saharanpur east
          [77.3, 28.9], // Meerut west
          [77.55, 28.55], // Dadri junction
        ],
      },
    },
  ],
}

export interface FreightNode {
  name: string
  corridor: string
  lat: number
  lon: number
  /** Corridor congestion at the node, 0–100%. */
  congestion: number
  /** Estimated daily emission impact from corridor freight activity. */
  dailyEmissionTonnes: number
}

export const FREIGHT_NODES: FreightNode[] = [
  {
    name: 'Manesar Logistics Hub',
    corridor: 'Western DFC · DMIC',
    lat: 28.35,
    lon: 76.94,
    congestion: 74,
    dailyEmissionTonnes: 820,
  },
  {
    name: 'Sanand Freight Node',
    corridor: 'DMIC Gujarat Spine',
    lat: 22.98,
    lon: 72.38,
    congestion: 58,
    dailyEmissionTonnes: 640,
  },
  {
    name: 'JNPT Gateway Terminal',
    corridor: 'Western DFC Terminus',
    lat: 18.95,
    lon: 72.95,
    congestion: 81,
    dailyEmissionTonnes: 1130,
  },
]

export function freightLinesFeatureCollection(): FeatureCollection<
  LineString,
  { name: string }
> {
  return FREIGHT_ROUTES
}

export function freightNodesFeatureCollection(): FeatureCollection<
  Point,
  { name: string; corridor: string; congestion: number; emission: number }
> {
  return {
    type: 'FeatureCollection',
    features: FREIGHT_NODES.map((node) => ({
      type: 'Feature',
      properties: {
        name: node.name,
        corridor: node.corridor,
        congestion: node.congestion,
        emission: node.dailyEmissionTonnes,
      },
      geometry: { type: 'Point', coordinates: [node.lon, node.lat] },
    })),
  }
}
