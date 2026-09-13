// The ONLY module allowed to call `fetch`. Every export here corresponds
// to one documented backend endpoint (docs/architecture.md's "API"
// section) and does nothing beyond typing the response — no pollution
// math, no derived/aggregated values. That logic lives in the backend;
// this module (and the rest of the frontend) only displays what it returns.

import type {
  AlertOut,
  CellDetailOut,
  Envelope,
  ForecastHorizonHours,
  ForecastOut,
  GridStateOut,
  WeatherReadingOut,
} from './types'

export const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

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

export function fetchGridCurrent(): Promise<Envelope<GridStateOut[]>> {
  return apiGet('/api/v1/grid/current')
}

export function fetchGridForecast(hours: ForecastHorizonHours): Promise<Envelope<ForecastOut[]>> {
  return apiGet(`/api/v1/grid/forecast?hours=${hours}`)
}

export function fetchWeather(): Promise<Envelope<WeatherReadingOut[]>> {
  return apiGet('/api/v1/weather')
}

export function fetchAlerts(): Promise<Envelope<AlertOut[]>> {
  return apiGet('/api/v1/alerts')
}

export function fetchCellDetail(h3Cell: string): Promise<Envelope<CellDetailOut>> {
  return apiGet(`/api/v1/cells/${encodeURIComponent(h3Cell)}`)
}
