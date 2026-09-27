// Local PM2.5 outlier triage over the currently loaded fine-resolution grid.
// This is a transparent spatial heuristic, not a satellite detector and not
// source attribution. Real candidates require fresh station support in the
// cell. Illustrative demo runs can exercise the same rule, but stay labeled.

import type { FeatureCollection, Point } from 'geojson'
import type { GridStateOut } from './types'
import { cellCenter, neighboringCells } from './h3Geometry'

const MIN_PM25_UGM3 = 60
const MIN_ABSOLUTE_DELTA_UGM3 = 25
const MIN_NEIGHBOR_RATIO = 1.5
const MIN_NEIGHBORS = 3
const MAX_OBSERVATION_AGE_HOURS = 6
const MIN_CONFIDENCE = 0.5

export interface LocalPollutionHotspot {
  h3Cell: string
  pm25: number
  nearbyMedian: number
  delta: number
  nearbyCellCount: number
  stationCount: number
  observationAgeHours: number | null
  confidence: number
  isDemo: boolean
}

/** Find fine-grid cells with a sharp PM2.5 increase against nearby cells.
 *  Values must be finite, reasonably supported, and compared with at least
 *  three neighbors. Production estimates also need a fresh station reading
 *  contributing to the candidate cell; demo output is allowed for a clearly
 *  identified walkthrough only. */
export function findLocalPollutionHotspots(
  cells: GridStateOut[],
  isDemoRun = false,
): LocalPollutionHotspot[] {
  const byCell = new Map(cells.map((cell) => [cell.h3_cell, cell]))
  const candidates: LocalPollutionHotspot[] = []

  for (const cell of cells) {
    const pm25 = cell.pm25
    const quality = cell.metadata?.quality
    const stationCount = quality?.observed_station_count ?? 0
    const observationAgeHours = quality?.max_observation_age_hours ?? null
    const isDemo = isDemoRun || cell.metadata?.synthetic === true

    if (
      pm25 === null ||
      !Number.isFinite(pm25) ||
      pm25 < MIN_PM25_UGM3 ||
      !Number.isFinite(cell.confidence) ||
      cell.confidence < MIN_CONFIDENCE
    ) {
      continue
    }

    if (
      !isDemo &&
      (stationCount < 1 ||
        observationAgeHours === null ||
        !Number.isFinite(observationAgeHours) ||
        observationAgeHours < 0 ||
        observationAgeHours > MAX_OBSERVATION_AGE_HOURS)
    ) {
      continue
    }

    const nearby = neighboringCells(cell.h3_cell)
      .map((neighbor) => byCell.get(neighbor)?.pm25)
      .filter(
        (value): value is number => value !== null && value !== undefined && Number.isFinite(value),
      )
      .sort((a, b) => a - b)
    if (nearby.length < MIN_NEIGHBORS) continue

    const middle = Math.floor(nearby.length / 2)
    const nearbyMedian =
      nearby.length % 2 === 0 ? (nearby[middle - 1] + nearby[middle]) / 2 : nearby[middle]
    const delta = pm25 - nearbyMedian
    if (
      delta < MIN_ABSOLUTE_DELTA_UGM3 ||
      nearbyMedian <= 0 ||
      pm25 / nearbyMedian < MIN_NEIGHBOR_RATIO
    ) {
      continue
    }

    candidates.push({
      h3Cell: cell.h3_cell,
      pm25,
      nearbyMedian,
      delta,
      nearbyCellCount: nearby.length,
      stationCount,
      observationAgeHours,
      confidence: cell.confidence,
      isDemo,
    })
  }

  return candidates.sort((a, b) => b.delta - a.delta || b.pm25 - a.pm25)
}

export function localHotspotsFeatureCollection(
  hotspots: LocalPollutionHotspot[],
): FeatureCollection<Point, Record<string, string | number | boolean | null>> {
  return {
    type: 'FeatureCollection',
    features: hotspots.map((hotspot) => {
      const [latitude, longitude] = cellCenter(hotspot.h3Cell)
      return {
        type: 'Feature',
        properties: {
          h3_cell: hotspot.h3Cell,
          pm25: hotspot.pm25,
          nearby_median: hotspot.nearbyMedian,
          delta: hotspot.delta,
          nearby_cell_count: hotspot.nearbyCellCount,
          station_count: hotspot.stationCount,
          observation_age_hours: hotspot.observationAgeHours,
          confidence: hotspot.confidence,
          is_demo: hotspot.isDemo,
        },
        geometry: { type: 'Point', coordinates: [longitude, latitude] },
      }
    }),
  }
}
