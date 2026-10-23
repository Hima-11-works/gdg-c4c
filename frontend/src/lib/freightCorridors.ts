import type { Feature, FeatureCollection, LineString } from 'geojson'

export interface CorridorEndpoint {
  label: string
  latitude: number
  longitude: number
}

/** Backend corridor definition; geometry provenance stays explicit in the UI. */
export interface FreightCorridorProperties {
  corridor_id: string
  name: string
  kind: string
  region: string
  geometry_source: string
  geometry_note: string
  geometry_description: string
  h3_resolution: number
  cell_count: number
  endpoints: CorridorEndpoint[]
  notes: string
}

export interface CorridorHorizonPoint {
  horizon_hours: number
  issued_at: string
  valid_at: string
}

export interface CorridorEventOut {
  event_id: string
  corridor_id: string
  corridor_name: string
  run_id: string
  run_mode: string
  run_synthetic: boolean
  issued_at: string
  horizons: CorridorHorizonPoint[]
  cell_count: number
  cells: string[]
  peak_predicted_ugm3: number | null
  peak_horizon_hours: number | null
  label_count: number
  label_sources: string[]
  prediction_methods: string[]
  model_versions: string[]
  dataset_versions: {
    dataset_id: string
    source: string
    product: string
    version: string
    kind: string
    region: string
    attribution: string
    license: string
  }[]
}

export interface CorridorEvaluationSlice {
  horizon_hours: number
  geography: string
  pairs: number
  station_count: number
  mae_ugm3: number | null
  rmse_ugm3: number | null
  bias_ugm3: number | null
  high_pollution_threshold_ugm3: number
  high_pollution_observed: number
  high_pollution_recall: number | null
  high_pollution_precision: number | null
  sufficient: boolean
  note: string
}

export interface CorridorEvaluationOut {
  verdict: string
  usable_as_real_world_evidence: boolean
  label_provenance: string
  reasons: string[]
  min_labels: number
  high_pollution_threshold_ugm3: number
  coverage: Record<string, number | null>
  slices: CorridorEvaluationSlice[]
  evidence: Record<string, unknown>[]
}

export interface CorridorCurrentOut {
  event: CorridorEventOut
  evaluation: CorridorEvaluationOut
}

export const FREIGHT_LINE_COLOR = '#00F5D4'

type CorridorFeatureProperties = Pick<
  FreightCorridorProperties,
  'corridor_id' | 'name' | 'geometry_source' | 'h3_resolution' | 'cell_count'
>

export function freightLinesFeatureCollection(
  corridors: FreightCorridorProperties[],
): FeatureCollection<LineString, CorridorFeatureProperties> {
  const features: Feature<LineString, CorridorFeatureProperties>[] = corridors.flatMap(
    (corridor) => {
      if (corridor.endpoints.length < 2) return []
      return [
        {
          type: 'Feature',
          id: corridor.corridor_id,
          properties: {
            corridor_id: corridor.corridor_id,
            name: corridor.name,
            geometry_source: corridor.geometry_source,
            h3_resolution: corridor.h3_resolution,
            cell_count: corridor.cell_count,
          },
          geometry: {
            type: 'LineString',
            coordinates: corridor.endpoints.map(
              ({ longitude, latitude }): [number, number] => [longitude, latitude],
            ),
          },
        },
      ]
    },
  )

  return { type: 'FeatureCollection', features }
}
