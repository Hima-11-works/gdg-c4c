// The ONLY module allowed to call `fetch`. Every export here corresponds
// to one documented backend endpoint (docs/architecture.md's "API"
// section) and does nothing beyond typing the response — no pollution
// math, no derived/aggregated values. That logic lives in the backend;
// this module (and the rest of the frontend) only displays what it returns.

import type {
  AlertOut,
  BoundingBox,
  CellDetailOut,
  Envelope,
  ForecastOut,
  GridStateOut,
  WeatherReadingOut,
} from './types'

export const API_BASE_URL: string =
  // `||`, not `??`: a hosting provider can inject an *empty* VITE_API_BASE_URL
  // (Vercel auto-imports .env.example), and an empty base URL would send
  // requests to the frontend's own origin. Fall back to the default instead.
  import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

interface ErrorResponseBody {
  error?: { code?: string; message?: string }
}

/** Thrown by every function in this module on a non-2xx response or a
 * network failure, so callers can handle "can't reach the backend" the
 * same way regardless of which endpoint failed. */
export class ApiError extends Error {
  readonly status: number
  readonly code: string

  constructor(status: number, code: string, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

/** A level-of-detail read: which H3 resolution to request, and which
 * viewport to scope it to (omitted entirely for the country tier — see
 * lib/lod.ts's `scopedToViewport` and app.api.deps.get_bbox_query on the
 * backend, which requires all four bbox params together or none). */
export interface LodQuery {
  resolution?: number
  bbox?: BoundingBox
}

function buildQuery(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) search.set(key, String(value))
  }
  const query = search.toString()
  return query ? `?${query}` : ''
}

function lodParams(query: LodQuery): Record<string, string | number | undefined> {
  return {
    resolution: query.resolution,
    min_lat: query.bbox?.minLat,
    min_lon: query.bbox?.minLon,
    max_lat: query.bbox?.maxLat,
    max_lon: query.bbox?.maxLon,
  }
}

async function apiGet<T>(path: string): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_BASE_URL}${path}`)
  } catch {
    throw new ApiError(0, 'network_error', 'Could not reach the backend. Is it running?')
  }

  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as ErrorResponseBody | null
    throw new ApiError(
      response.status,
      body?.error?.code ?? 'http_error',
      body?.error?.message ?? `Request failed with status ${response.status}`,
    )
  }

  return response.json() as Promise<T>
}

export function fetchGridCurrent(query: LodQuery = {}): Promise<Envelope<GridStateOut[]>> {
  return apiGet(`/api/v1/grid/current${buildQuery(lodParams(query))}`)
}

export function fetchGridForecast(
  minutes: number,
  query: LodQuery = {},
): Promise<Envelope<ForecastOut[]>> {
  return apiGet(`/api/v1/grid/forecast${buildQuery({ minutes, ...lodParams(query) })}`)
}

export function fetchWeather(query: LodQuery = {}): Promise<Envelope<WeatherReadingOut[]>> {
  return apiGet(`/api/v1/weather${buildQuery(lodParams(query))}`)
}

export function fetchAlerts(): Promise<Envelope<AlertOut[]>> {
  return apiGet('/api/v1/alerts')
}

export function fetchCellDetail(
  h3Cell: string,
  resolution?: number,
): Promise<Envelope<CellDetailOut>> {
  return apiGet(`/api/v1/cells/${encodeURIComponent(h3Cell)}${buildQuery({ resolution })}`)
}
