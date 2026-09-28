// Major economic freight corridors — Western DFC / DMIC alignment — as
// hand-authored GeoJSON for an institutional-knowledge overlay. These are
// simplified illustrative geometries standing in for the real corridor
// network until a routes endpoint exists; the feature shape matches what
// such an endpoint should return.
//
// Overlays like this are contextual information for intervention planning
// and inspection targeting — never a relocation recommendation.

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
  /** Illustrative scenario value, not measured corridor congestion. */
  congestion: number
  /** Illustrative sample value, not measured daily emissions. */
  dailyEmissionTonnes: number
  /** Illustrative scenario label, not a forecast. */
  spikeRisk: 'Elevated' | 'High' | 'Severe'
  /** Example response text, not official or statutory guidance. */
  advisory: string
}

export const FREIGHT_NODES: FreightNode[] = [
  {
    name: 'Manesar Logistics Hub',
    corridor: 'Western DFC · DMIC (Delhi–Mumbai)',
    lat: 28.35,
    lon: 76.94,
    congestion: 78,
    dailyEmissionTonnes: 890,
    spikeRisk: 'Severe',
    advisory:
      'Downwind industrial & vehicular entrapment; recommend low-emission freight scheduling.',
  },
  {
    name: 'Sanand Freight Node',
    corridor: 'DMIC Gujarat Industrial Spine',
    lat: 22.98,
    lon: 72.38,
    congestion: 58,
    dailyEmissionTonnes: 640,
    spikeRisk: 'Elevated',
    advisory: 'Steady maritime trade flow; ambient particulates within operational limits.',
  },
  {
    name: 'Surat Industrial Transit Hub',
    corridor: 'Western DFC Chemical & Textile Spine',
    lat: 21.17,
    lon: 72.83,
    congestion: 72,
    dailyEmissionTonnes: 810,
    spikeRisk: 'High',
    advisory: 'Heavy diesel transit corridor; recommend speed regulation and dry sweeping.',
  },
  {
    name: 'JNPT Gateway Terminal',
    corridor: 'Western DFC Terminus (Mumbai)',
    lat: 18.95,
    lon: 72.95,
    congestion: 84,
    dailyEmissionTonnes: 1180,
    spikeRisk: 'High',
    advisory: 'Port-bound drayage emissions; enforce shore power and idle-reduction directives.',
  },
  {
    name: 'Ludhiana Cargo Logistics Park',
    corridor: 'Eastern DFC Northern Origin',
    lat: 30.9,
    lon: 75.85,
    congestion: 76,
    dailyEmissionTonnes: 720,
    spikeRisk: 'Severe',
    advisory: 'Seasonal stubble burning & industrial smoke; activate freight bypass routing.',
  },
  {
    name: 'Dadri Multimodal Hub',
    corridor: 'WDFC / EDFC Strategic Interchange',
    lat: 28.55,
    lon: 77.55,
    congestion: 88,
    dailyEmissionTonnes: 1250,
    spikeRisk: 'Severe',
    advisory: 'Critical interchange junction; stage-triggered truck diversion protocols in effect.',
  },
  {
    name: 'Kanpur Freight Terminal',
    corridor: 'Eastern DFC (Indo-Gangetic Spine)',
    lat: 26.45,
    lon: 80.33,
    congestion: 69,
    dailyEmissionTonnes: 790,
    spikeRisk: 'High',
    advisory: 'Winter inversion layer vulnerability; strict boiler & stack emission vigil.',
  },
  {
    name: 'Dankuni Eastern Freight Terminal',
    corridor: 'Eastern DFC Maritime Gateway (Kolkata)',
    lat: 22.68,
    lon: 88.29,
    congestion: 64,
    dailyEmissionTonnes: 710,
    spikeRisk: 'Elevated',
    advisory: 'Riverine basin dispersion; monitor thermal power plant stack plumes.',
  },
]

export function freightLinesFeatureCollection(): FeatureCollection<LineString, { name: string }> {
  return FREIGHT_ROUTES
}

export function freightNodesFeatureCollection(): FeatureCollection<
  Point,
  {
    name: string
    corridor: string
    congestion: number
    emission: number
    spikeRisk: string
    advisory: string
  }
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
        spikeRisk: node.spikeRisk,
        advisory: node.advisory,
      },
      geometry: { type: 'Point', coordinates: [node.lon, node.lat] },
    })),
  }
}
