// Major economic freight corridors — interactive, predictive corridor system
// representing major Indian corridors with interstate air-quality forecasting
// and logistics impacts.
//
// These corridors serve as strategic overlays for interstate freight planning,
// proactive routing diversions, and cross-border emission coordination.

import type { Feature, FeatureCollection, LineString, Point } from 'geojson'

export type CorridorStatus = 'Critical' | 'Warning' | 'Normal'

export interface FreightCorridorProperties {
  id: string
  name: string
  code: string
  status: CorridorStatus
  forecast_alert: string
  affected_states: string[]
  length_km: number
  key_nodes: string[]
  forecast_window: string
  primary_pollutant: string
  est_freight_delay: string
  interstate_coordination: string
  air_quality_index_forecast: number
  visibility_min_meters: number
  recommended_action: string
  speed_impact_pct: number
  economic_daily_tonnes: number
}

export const FREIGHT_LINE_COLOR = '#00F5D4'

export const STATUS_COLORS: Record<CorridorStatus, string> = {
  Critical: '#EF4444', // Red for Critical / AKIC
  Warning: '#F59E0B',  // Amber for Warning
  Normal: '#00F5D4',   // Cyan for Normal
}

export const FREIGHT_CORRIDORS: Feature<LineString, FreightCorridorProperties>[] = [
  {
    type: 'Feature',
    id: 'akic',
    properties: {
      id: 'akic',
      name: 'Amritsar-Kolkata Industrial Corridor (AKIC)',
      code: 'AKIC',
      status: 'Critical',
      forecast_alert:
        'Amritsar-Kolkata Corridor: Severe PM2.5 spike forecasted between Kanpur and Varanasi in +14 hours. Visibility expected to drop below 200m.',
      affected_states: ['Punjab', 'Haryana', 'UP', 'Bihar', 'Jharkhand', 'West Bengal'],
      length_km: 1839,
      key_nodes: [
        'Amritsar',
        'Jalandhar',
        'Ludhiana',
        'Ambala',
        'Saharanpur',
        'Meerut',
        'Aligarh',
        'Kanpur',
        'Prayagraj',
        'Varanasi',
        'Gaya',
        'Dhanbad',
        'Asansol',
        'Kolkata (Dankuni)',
      ],
      forecast_window: '+14 Hours',
      primary_pollutant: 'PM2.5 (Severe Inversion Spike)',
      est_freight_delay: '4.5 – 6.0 hrs',
      interstate_coordination:
        'UP-Bihar-WB Cross-Border Joint Air Quality Taskforce Activated (Protocol CAQM-3)',
      air_quality_index_forecast: 442,
      visibility_min_meters: 180,
      recommended_action:
        'Enact Stage-IV emergency freight curfew. Reroute Class-6+ diesel transit via southern ring corridors; halt non-perishable freight loading at Kanpur & Varanasi hubs.',
      speed_impact_pct: -45,
      economic_daily_tonnes: 18500,
    },
    geometry: {
      type: 'LineString',
      coordinates: [
        [74.8723, 31.634],  // Amritsar
        [75.5762, 31.326],  // Jalandhar
        [75.8573, 30.901],  // Ludhiana
        [76.7794, 30.3782], // Ambala
        [77.546, 29.964],   // Saharanpur
        [77.7064, 28.9845], // Meerut
        [77.55, 28.55],     // Dadri / Greater Noida
        [78.088, 27.8974],  // Aligarh
        [79.03, 27.37],     // Farrukhabad junction
        [80.3319, 26.4499], // Kanpur
        [81.25, 25.92],     // Fatehpur
        [81.8463, 25.4358], // Prayagraj
        [82.9739, 25.3176], // Varanasi
        [83.116, 25.282],   // Mughalsarai (Pt DDU)
        [84.0315, 24.952],  // Sasaram
        [85.0002, 24.7914], // Gaya
        [86.4304, 23.7957], // Dhanbad
        [86.9842, 23.6739], // Asansol
        [87.855, 23.232],   // Bardhaman
        [88.3639, 22.5726], // Kolkata (Dankuni)
      ],
    },
  },
  {
    type: 'Feature',
    id: 'dmic',
    properties: {
      id: 'dmic',
      name: 'Delhi-Mumbai Industrial Corridor (DMIC)',
      code: 'DMIC',
      status: 'Warning',
      forecast_alert:
        'Delhi-Mumbai Corridor: Elevated particulate & fugitive dust suspension forecasted across Rewari-Jaipur stretch in +8 hours. Visibility ~550m.',
      affected_states: ['Delhi', 'Haryana', 'Rajasthan', 'Gujarat', 'Maharashtra'],
      length_km: 1504,
      key_nodes: [
        'Dadri / Delhi NCR',
        'Manesar',
        'Rewari',
        'Neemrana',
        'Jaipur',
        'Ajmer',
        'Palanpur',
        'Ahmedabad',
        'Vadodara',
        'Surat',
        'Vapi',
        'JNPT Mumbai',
      ],
      forecast_window: '+8 Hours',
      primary_pollutant: 'PM10 & Vehicular Nitrogen Oxides (NOx)',
      est_freight_delay: '1.5 – 2.5 hrs',
      interstate_coordination:
        'NCR-Rajasthan-Gujarat Interstate Air Basin Logistics Monitoring In Effect',
      air_quality_index_forecast: 285,
      visibility_min_meters: 550,
      recommended_action:
        'Apply staggered night transit dispatches between 22:00 and 04:00; mandate continuous mist cannon operation along desert-border freight corridors.',
      speed_impact_pct: -20,
      economic_daily_tonnes: 22400,
    },
    geometry: {
      type: 'LineString',
      coordinates: [
        [77.209, 28.6139],  // Delhi NCR
        [77.55, 28.55],     // Dadri Multimodal Hub
        [76.938, 28.354],   // Manesar
        [76.618, 28.192],   // Rewari
        [76.385, 27.988],   // Neemrana
        [75.7873, 26.9124], // Jaipur
        [74.6399, 26.4499], // Ajmer
        [73.535, 25.733],   // Marwar
        [72.4333, 24.1724], // Palanpur
        [72.3693, 23.588],  // Mehsana
        [72.5714, 23.0225], // Ahmedabad
        [73.1812, 22.3072], // Vadodara
        [72.9959, 21.7051], // Bharuch
        [72.8311, 21.1702], // Surat
        [72.9106, 20.3893], // Vapi
        [72.95, 18.95],     // JNPT Terminal Mumbai
      ],
    },
  },
  {
    type: 'Feature',
    id: 'edfc',
    properties: {
      id: 'edfc',
      name: 'Eastern Dedicated Freight Corridor (EDFC)',
      code: 'EDFC',
      status: 'Warning',
      forecast_alert:
        'Eastern DFC: High seasonal aerosol entrapment forecast along Sahnewal-Khurja section in +6 hours; heavy container movement under cautionary speed restriction.',
      affected_states: ['Punjab', 'Haryana', 'Uttar Pradesh', 'Bihar', 'Jharkhand', 'West Bengal'],
      length_km: 1875,
      key_nodes: [
        'Sahnewal (Ludhiana)',
        'Shambhu',
        'Khurja Junction',
        'Tundla',
        'Kanpur Outer',
        'Fatehpur',
        'Prayagraj Chheoki',
        'Pt. DDU',
        'Sonnagar',
        'Gomoh',
        'Andal',
        'Dankuni',
      ],
      forecast_window: '+6 Hours',
      primary_pollutant: 'PM2.5 & Agricultural Biomass Smoke',
      est_freight_delay: '2.0 – 3.0 hrs',
      interstate_coordination:
        'Northern & Eastern Dedicated Freight Railway Corridor Central Control Coordination',
      air_quality_index_forecast: 318,
      visibility_min_meters: 420,
      recommended_action:
        'Cap electric freight locomotives at 65 km/h across foggy sections; sequence freight rakes to avoid terminal choking at Khurja interchange.',
      speed_impact_pct: -25,
      economic_daily_tonnes: 26500,
    },
    geometry: {
      type: 'LineString',
      coordinates: [
        [75.98, 30.85],  // Sahnewal (Ludhiana)
        [76.6, 30.3],    // Shambhu / Ambala East
        [77.1, 29.8],    // Yamunanagar
        [77.5, 29.1],    // Meerut East
        [77.85, 28.25],  // Khurja Junction
        [78.24, 27.2],   // Tundla
        [80.4, 26.4],    // Kanpur Outer
        [80.83, 25.93],  // Fatehpur bypass
        [81.89, 25.38],  // Prayagraj Chheoki
        [82.56, 25.14],  // Mirzapur
        [83.15, 25.28],  // Pt. Deen Dayal Upadhyaya
        [84.18, 24.92],  // Sonnagar
        [85.5, 24.3],    // Koderma
        [86.15, 23.87],  // Gomoh
        [87.19, 23.58],  // Andal
        [88.3, 22.68],   // Dankuni
      ],
    },
  },
  {
    type: 'Feature',
    id: 'ecec',
    properties: {
      id: 'ecec',
      name: 'East Coast Economic Corridor (ECEC - Odisha/Andhra)',
      code: 'ECEC',
      status: 'Normal',
      forecast_alert:
        'East Coast Economic Corridor: Favorable onshore maritime breeze maintaining PM2.5 in Good-to-Moderate band across Paradip-Vizag-Chennai segment.',
      affected_states: ['West Bengal', 'Odisha', 'Andhra Pradesh', 'Tamil Nadu'],
      length_km: 1700,
      key_nodes: [
        'Kolkata',
        'Kharagpur',
        'Balasore',
        'Bhubaneswar',
        'Gopalpur',
        'Visakhapatnam',
        'Kakinada',
        'Rajahmundry',
        'Vijayawada',
        'Nellore',
        'Chennai',
      ],
      forecast_window: '+24 Hours',
      primary_pollutant: 'Marine Aerosols & Low Particulates',
      est_freight_delay: 'Nominal (< 15 min)',
      interstate_coordination:
        'ADB / MoPSW Multi-State Maritime Gateway Corridor Framework Operational',
      air_quality_index_forecast: 62,
      visibility_min_meters: 4800,
      recommended_action:
        'Unrestricted multimodal logistics throughput; prioritize port-bound export container movements along NH-16 spine.',
      speed_impact_pct: 0,
      economic_daily_tonnes: 16800,
    },
    geometry: {
      type: 'LineString',
      coordinates: [
        [88.3639, 22.5726], // Kolkata
        [87.3215, 22.346],  // Kharagpur
        [86.9324, 21.4934], // Balasore
        [85.883, 20.4625],  // Cuttack
        [85.8245, 20.2961], // Bhubaneswar
        [84.7941, 19.3149], // Gopalpur / Berhampur
        [83.8938, 18.2949], // Srikakulam
        [83.2185, 17.6868], // Visakhapatnam Port
        [82.2475, 16.9891], // Kakinada
        [81.804, 17.0005],  // Rajahmundry
        [80.648, 16.5062],  // Vijayawada
        [80.0499, 15.5057], // Ongole
        [79.9865, 14.4426], // Nellore
        [80.2707, 13.0827], // Chennai
      ],
    },
  },
  {
    type: 'Feature',
    id: 'cbic',
    properties: {
      id: 'cbic',
      name: 'Chennai-Bengaluru Industrial Corridor (CBIC)',
      code: 'CBIC',
      status: 'Normal',
      forecast_alert:
        'Chennai-Bengaluru Corridor: Mild thermal inversion predicted in Hoskote industrial belt in +12 hours; local PM2.5 elevation without freight disruption.',
      affected_states: ['Tamil Nadu', 'Andhra Pradesh', 'Karnataka'],
      length_km: 560,
      key_nodes: [
        'Chennai Port',
        'Sriperumbudur',
        'Ranipet',
        'Chittoor',
        'Palamaner',
        'Bangarapet',
        'Hoskote',
        'Bengaluru',
      ],
      forecast_window: '+12 Hours',
      primary_pollutant: 'Vehicular Particulates & Ground Ozone',
      est_freight_delay: 'Nominal (< 30 min)',
      interstate_coordination:
        'TN-AP-Karnataka Tri-State Industrial Expressway Environmental Oversight Unit',
      air_quality_index_forecast: 78,
      visibility_min_meters: 3900,
      recommended_action:
        'Encourage night transit for heavy industrial cargo into Bengaluru Peripheral Road; routine ambient monitoring active.',
      speed_impact_pct: -5,
      economic_daily_tonnes: 14500,
    },
    geometry: {
      type: 'LineString',
      coordinates: [
        [80.2707, 13.0827], // Chennai
        [79.9424, 12.9699], // Sriperumbudur
        [79.3326, 12.9274], // Ranipet
        [79.1003, 13.2172], // Chittoor
        [78.749, 13.2],     // Palamaner
        [78.2, 12.99],      // Bangarapet / Kolar
        [77.7981, 13.07],   // Hoskote
        [77.5946, 12.9716], // Bengaluru
      ],
    },
  },
]

export const FREIGHT_ROUTES: FeatureCollection<LineString, FreightCorridorProperties> = {
  type: 'FeatureCollection',
  features: FREIGHT_CORRIDORS,
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
    corridor: 'DMIC (Delhi–Mumbai)',
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
    corridor: 'DMIC Chemical & Textile Spine',
    lat: 21.17,
    lon: 72.83,
    congestion: 72,
    dailyEmissionTonnes: 810,
    spikeRisk: 'High',
    advisory: 'Heavy diesel transit corridor; recommend speed regulation and dry sweeping.',
  },
  {
    name: 'JNPT Gateway Terminal',
    corridor: 'DMIC Terminus (Mumbai)',
    lat: 18.95,
    lon: 72.95,
    congestion: 84,
    dailyEmissionTonnes: 1180,
    spikeRisk: 'High',
    advisory: 'Port-bound drayage emissions; enforce shore power and idle-reduction directives.',
  },
  {
    name: 'Ludhiana Cargo Logistics Park',
    corridor: 'AKIC / EDFC Northern Origin',
    lat: 30.9,
    lon: 75.85,
    congestion: 76,
    dailyEmissionTonnes: 720,
    spikeRisk: 'Severe',
    advisory: 'Seasonal stubble burning & industrial smoke; activate freight bypass routing.',
  },
  {
    name: 'Dadri Multimodal Hub',
    corridor: 'DMIC / AKIC Strategic Interchange',
    lat: 28.55,
    lon: 77.55,
    congestion: 88,
    dailyEmissionTonnes: 1250,
    spikeRisk: 'Severe',
    advisory: 'Critical interchange junction; stage-triggered truck diversion protocols in effect.',
  },
  {
    name: 'Kanpur Freight Terminal',
    corridor: 'AKIC / EDFC (Indo-Gangetic Spine)',
    lat: 26.45,
    lon: 80.33,
    congestion: 89,
    dailyEmissionTonnes: 1420,
    spikeRisk: 'Severe',
    advisory:
      'Severe PM2.5 spike predicted in +14h. Visibility <200m. Heavy freight curfew alert active.',
  },
  {
    name: 'Varanasi Multimodal Terminal',
    corridor: 'AKIC Eastern Core',
    lat: 25.32,
    lon: 82.97,
    congestion: 82,
    dailyEmissionTonnes: 980,
    spikeRisk: 'Severe',
    advisory: 'High inversion layer vulnerability; strict multi-state transit coordination required.',
  },
  {
    name: 'Visakhapatnam Port Logistics Hub',
    corridor: 'East Coast Economic Corridor (ECEC)',
    lat: 17.69,
    lon: 83.22,
    congestion: 52,
    dailyEmissionTonnes: 580,
    spikeRisk: 'Elevated',
    advisory: 'Steady maritime dispersion; clear freight transit flow.',
  },
  {
    name: 'Sriperumbudur Auto Logistics Enclave',
    corridor: 'Chennai-Bengaluru Industrial Corridor (CBIC)',
    lat: 12.97,
    lon: 79.94,
    congestion: 61,
    dailyEmissionTonnes: 620,
    spikeRisk: 'Elevated',
    advisory: 'Moderate traffic corridor; ambient quality within statutory guidelines.',
  },
  {
    name: 'Dankuni Eastern Freight Terminal',
    corridor: 'AKIC / EDFC Gateway (Kolkata)',
    lat: 22.68,
    lon: 88.29,
    congestion: 68,
    dailyEmissionTonnes: 750,
    spikeRisk: 'Elevated',
    advisory: 'Riverine basin dispersion; monitor thermal power plant stack plumes.',
  },
]

export function freightLinesFeatureCollection(): FeatureCollection<
  LineString,
  FreightCorridorProperties
> {
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

/** Safely normalizes raw feature properties received from MapLibre click events. */
export function normalizeCorridorProperties(
  raw: Record<string, unknown>,
): FreightCorridorProperties {
  const match = FREIGHT_CORRIDORS.find(
    (c) => c.properties.id === raw.id || c.properties.name === raw.name || c.properties.code === raw.code,
  )

  const parseArray = (val: unknown, fallback: string[]): string[] => {
    if (Array.isArray(val)) return val as string[]
    if (typeof val === 'string') {
      try {
        const parsed = JSON.parse(val)
        if (Array.isArray(parsed)) return parsed as string[]
      } catch {
        return val.split(',').map((s) => s.trim())
      }
    }
    return fallback
  }

  if (match) {
    return {
      ...match.properties,
      ...raw,
      id: (raw.id as string) || match.properties.id,
      name: (raw.name as string) || match.properties.name,
      code: (raw.code as string) || match.properties.code,
      status: (raw.status as CorridorStatus) || match.properties.status,
      forecast_alert: (raw.forecast_alert as string) || match.properties.forecast_alert,
      affected_states: parseArray(raw.affected_states, match.properties.affected_states),
      key_nodes: parseArray(raw.key_nodes, match.properties.key_nodes),
    } as FreightCorridorProperties
  }

  return {
    id: String(raw.id || 'corridor'),
    name: String(raw.name || 'Economic Corridor'),
    code: String(raw.code || 'CORR'),
    status: (raw.status as CorridorStatus) || 'Normal',
    forecast_alert: String(raw.forecast_alert || ''),
    affected_states: parseArray(raw.affected_states, []),
    length_km: Number(raw.length_km || 1000),
    key_nodes: parseArray(raw.key_nodes, []),
    forecast_window: String(raw.forecast_window || '+12 Hours'),
    primary_pollutant: String(raw.primary_pollutant || 'PM2.5'),
    est_freight_delay: String(raw.est_freight_delay || 'Nominal'),
    interstate_coordination: String(raw.interstate_coordination || 'Active Monitoring'),
    air_quality_index_forecast: Number(raw.air_quality_index_forecast || 100),
    visibility_min_meters: Number(raw.visibility_min_meters || 2000),
    recommended_action: String(raw.recommended_action || 'Maintain standard transit monitoring.'),
    speed_impact_pct: Number(raw.speed_impact_pct || 0),
    economic_daily_tonnes: Number(raw.economic_daily_tonnes || 10000),
  }
}
