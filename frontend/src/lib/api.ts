// Every read and write of our own backend API lives here. Each export
// corresponds to one documented backend route (docs/architecture.md's "API"
// section) and returns it typed for the UI; where a response's row shape
// differs from the view model the map and drawer already consume, the small
// adapter sits with the export that needs it (see fetchActiveFires). No
// pollution math and no aggregation: derived values come from the backend,
// and this module - like the rest of the frontend - only displays them.
//
// `fetch` does appear outside this module, but never for API reads:
// components/MapView.tsx fetches the basemap style, and lib/locations.ts +
// lib/stateBoundaries.ts fetch the bundled /data/* assets. Anything that
// talks to the backend API belongs here instead.

import type {
  AlertOut,
  BoundingBox,
  CellDetailOut,
  CellSatelliteAnalysisOut,
  Envelope,
  FireHotspotOut,
  FireReportOut,
  FireReportStatus,
  FireReportSubmit,
  FireReportWithStatus,
  ForecastOut,
  FederationStatusOut,
  HotspotCatalogOut,
  HotspotEventsOut,
  HotspotScanOut,
  ForecastV2Out,
  GridCurrentV2Out,
  GridStateOut,
  MetaV2Out,
  V2Envelope,
  CellDetailV2Out,
  WeatherV2Out,
  WeatherReadingOut,
} from './types'
import { activeFireFromHotspot } from './activeFires'
import type { ActiveFire } from './activeFires'

// `||` (not `??`) so an empty VITE_API_BASE_URL — which a host may inject
// when auto-importing env files — still falls back, and any trailing slash is
// stripped so `${API_BASE_URL}${path}` never becomes `//api/v1/...`.
export const API_BASE_URL: string = (
  import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'
).replace(/\/+$/, '')

interface ErrorResponseBody {
  error?: { code?: string; message?: string }
  detail?: string
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

async function apiGet<T>(
  path: string,
  signal?: AbortSignal,
  onProgress?: (loaded: number, total: number) => void,
): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_BASE_URL}${path}`, { signal })
  } catch (error) {
    if (signal?.aborted) throw error
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

  if (!onProgress || !response.body) return response.json() as Promise<T>

  const reader = response.body.getReader()
  const total = Number(response.headers.get('content-length')) || 0
  const chunks: Uint8Array[] = []
  let loaded = 0
  onProgress(0, total)
  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      chunks.push(value)
      loaded += value.byteLength
      onProgress(loaded, total)
    }
  } finally {
    reader.releaseLock()
  }

  const bytes = new Uint8Array(loaded)
  let offset = 0
  for (const chunk of chunks) {
    bytes.set(chunk, offset)
    offset += chunk.byteLength
  }
  try {
    return JSON.parse(new TextDecoder().decode(bytes)) as T
  } catch {
    throw new ApiError(0, 'invalid_response', 'The backend returned invalid cell data.')
  }
}

let cachedPublishedMeta: MetaV2Out | null = null
let publishedRunExpiresAt = 0
let publishedRunRequest: Promise<MetaV2Out> | null = null

/** Resolve one published run id for all map and cell reads. Refreshing the
 * pointer every few minutes lets hourly publications appear without allowing
 * independently-polled resources to jump between runs mid-refresh. */
async function publishedMeta(): Promise<MetaV2Out> {
  if (cachedPublishedMeta !== null && Date.now() < publishedRunExpiresAt) {
    return cachedPublishedMeta
  }
  if (publishedRunRequest !== null) return publishedRunRequest
  publishedRunRequest = apiGet<MetaV2Out>('/api/v2/meta')
    .then((meta) => {
      cachedPublishedMeta = meta
      publishedRunExpiresAt = Date.now() + 5 * 60 * 1000
      return meta
    })
    .finally(() => {
      publishedRunRequest = null
    })
  return publishedRunRequest
}

async function publishedRunId(): Promise<string> {
  return (await publishedMeta()).latest_run_id
}

export async function fetchPublishedMeta(): Promise<Envelope<MetaV2Out>> {
  const meta = await publishedMeta()
  return {
    generated_at: meta.generated_at,
    is_demo: meta.data_mode === 'demo',
    run_id: meta.latest_run_id,
    mode: meta.data_mode,
    data: meta,
  }
}

async function apiGetV2<T>(
  path: string,
  pinnedRunId?: string,
  signal?: AbortSignal,
): Promise<V2Envelope<T>> {
  const runId = pinnedRunId ?? (await publishedRunId())
  if (signal?.aborted) throw signal.reason ?? new DOMException('Aborted', 'AbortError')
  const [pathname, queryString] = path.split('?', 2)
  const query = new URLSearchParams(queryString ?? '')
  query.set('run_id', runId)
  return apiGet<V2Envelope<T>>(`${pathname}?${query.toString()}`, signal)
}

type GridLoadProgress = (loaded: number, total: number) => void

interface GridStreamComplete<T> {
  type: 'complete'
  generated_at: string
  run_id: string
  mode: V2Envelope<T>['mode']
  is_demo: boolean
  attribution: V2Envelope<T>['attribution']
  coverage: V2Envelope<T>['coverage']
}

/** Read a cell-counted NDJSON response. Each batch updates progress using
 * the exact number of processed H3 cells rather than elapsed-time estimates. */
async function apiGetV2GridStream<T>(
  path: string,
  pinnedRunId: string | undefined,
  signal: AbortSignal | undefined,
  onProgress?: GridLoadProgress,
): Promise<V2Envelope<T[]>> {
  onProgress?.(0, 0)
  const runId = pinnedRunId ?? (await publishedRunId())
  if (signal?.aborted) throw signal.reason ?? new DOMException('Aborted', 'AbortError')
  const [pathname, queryString] = path.split('?', 2)
  const query = new URLSearchParams(queryString ?? '')
  query.set('run_id', runId)

  let response: Response
  try {
    response = await fetch(`${API_BASE_URL}${pathname}?${query.toString()}`, { signal })
  } catch (error) {
    if (signal?.aborted) throw error
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

  const reader = response.body?.getReader()
  if (!reader) throw new ApiError(0, 'stream_unavailable', 'The backend did not stream cell data.')

  const decoder = new TextDecoder()
  const data: T[] = []
  let buffered = ''
  let complete: GridStreamComplete<T> | null = null

  const yieldToPaint = () =>
    new Promise<void>((resolve) => {
      if (typeof requestAnimationFrame === 'function') requestAnimationFrame(() => resolve())
      else setTimeout(resolve, 0)
    })

  const consumeLine = async (line: string) => {
    if (!line.trim()) return
    const event = JSON.parse(line) as
      | { type: 'start'; total: number }
      | { type: 'batch'; loaded: number; total: number; data: T[] }
      | GridStreamComplete<T>
    if (event.type === 'start') {
      onProgress?.(0, event.total)
    } else if (event.type === 'batch') {
      data.push(...event.data)
      onProgress?.(event.loaded, event.total)
      // Let React paint each received batch even when the browser coalesces
      // several NDJSON records into one network read.
      await yieldToPaint()
    } else if (event.type === 'complete') {
      complete = event
    }
  }

  while (true) {
    const { value, done } = await reader.read()
    buffered += decoder.decode(value, { stream: !done })
    let newline = buffered.indexOf('\n')
    while (newline !== -1) {
      await consumeLine(buffered.slice(0, newline))
      buffered = buffered.slice(newline + 1)
      newline = buffered.indexOf('\n')
    }
    if (done) break
  }
  await consumeLine(buffered)
  reader.releaseLock()

  if (complete === null)
    throw new ApiError(0, 'incomplete_stream', 'The backend stream ended before all cells loaded.')
  const metadata = complete as GridStreamComplete<T>
  onProgress?.(
    metadata.coverage?.requested_cells ?? data.length,
    metadata.coverage?.requested_cells ?? data.length,
  )
  return {
    generated_at: metadata.generated_at,
    run_id: metadata.run_id,
    mode: metadata.mode,
    is_demo: metadata.is_demo,
    attribution: metadata.attribution,
    coverage: metadata.coverage,
    data,
  }
}

function preserveV2Envelope<T, U>(envelope: V2Envelope<T>, data: U): Envelope<U> {
  return {
    generated_at: envelope.generated_at,
    is_demo: envelope.is_demo,
    data,
    run_id: envelope.run_id,
    mode: envelope.mode,
    attribution: envelope.attribution,
    coverage: envelope.coverage,
  }
}

function fromCurrentV2(cell: GridCurrentV2Out): GridStateOut {
  return {
    h3_cell: cell.h3_cell,
    timestamp: cell.valid_at,
    confidence: cell.confidence,
    pm25: cell.pm25,
    pdi: cell.pdi,
    wind_speed: cell.wind_speed_ms,
    wind_direction: cell.wind_direction_deg,
    latitude: cell.latitude,
    longitude: cell.longitude,
    metadata: cell.metadata,
    exposure: cell.exposure,
  }
}

function fromForecastV2(forecast: ForecastV2Out): ForecastOut {
  return {
    h3_cell: forecast.h3_cell,
    generated_at: forecast.generated_at,
    forecast_time: forecast.forecast_time,
    forecast_hours: forecast.forecast_hours,
    forecast_minutes: Math.round(forecast.forecast_hours * 60),
    predicted_pm25: forecast.predicted_pm25,
    confidence: forecast.confidence,
    lower_pm25: forecast.lower_pm25,
    upper_pm25: forecast.upper_pm25,
    metadata: forecast.metadata,
    exposure: forecast.exposure,
  }
}

function fromWeatherV2(weather: WeatherV2Out): WeatherReadingOut {
  return {
    h3_cell: weather.h3_cell,
    latitude: weather.latitude,
    longitude: weather.longitude,
    wind_speed: weather.wind_speed_ms,
    wind_direction: weather.wind_direction_deg,
    precipitation: weather.precipitation_mm,
    boundary_layer_height: weather.boundary_layer_height_m,
    temperature: weather.temperature_c,
    humidity: weather.relative_humidity_pct,
    measured_at: weather.valid_at,
    wind_u_ms: weather.wind_u_ms,
    wind_v_ms: weather.wind_v_ms,
    issued_at: weather.issued_at,
    valid_at: weather.valid_at,
  }
}

/** The app's only write call: POST /api/v1/reports (citizen fire reports). */
async function apiPost<T>(path: string, payload: unknown): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
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

/** Review a persisted hotspot event as an authenticated pollution-control actor. */
export function reviewHotspotEvent(
  eventId: string,
  reviewState: 'confirmed' | 'dismissed',
  credentials: { simulatorKey: string; actorId: string },
): Promise<Envelope<HotspotEventsOut['events'][number]>> {
  const path = `/api/v1/hotspots/events/${encodeURIComponent(eventId)}/review`
  return fetch(`${API_BASE_URL}${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Simulator-Key': credentials.simulatorKey,
      'X-Actor-Id': credentials.actorId,
    },
    body: JSON.stringify({ review_state: reviewState }),
  })
    .then(async (response) => {
      if (!response.ok) {
        const body = (await response.json().catch(() => null)) as ErrorResponseBody | null
        throw new ApiError(
          response.status,
          body?.error?.code ?? 'http_error',
          body?.error?.message ?? body?.detail ?? `Review failed with status ${response.status}`,
        )
      }
      return response.json() as Promise<Envelope<HotspotEventsOut['events'][number]>>
    })
    .catch((error: unknown) => {
      if (error instanceof ApiError) throw error
      throw new ApiError(0, 'network_error', 'Could not reach the backend. Is it running?')
    })
}

export function fetchGridCurrent(
  query: LodQuery = {},
  runId?: string,
  signal?: AbortSignal,
  onProgress?: GridLoadProgress,
): Promise<Envelope<GridStateOut[]>> {
  return apiGetV2GridStream<GridCurrentV2Out>(
    `/api/v2/grid/current/stream${buildQuery(lodParams(query))}`,
    runId,
    signal,
    onProgress,
  ).then((envelope) => preserveV2Envelope(envelope, envelope.data.map(fromCurrentV2)))
}

export function fetchGridForecast(
  minutes: number,
  query: LodQuery = {},
  runId?: string,
  signal?: AbortSignal,
  onProgress?: GridLoadProgress,
): Promise<Envelope<ForecastOut[]>> {
  return apiGetV2GridStream<ForecastV2Out>(
    `/api/v2/grid/forecast/stream${buildQuery({ hours: minutes / 60, ...lodParams(query) })}`,
    runId,
    signal,
    onProgress,
  ).then((envelope) => preserveV2Envelope(envelope, envelope.data.map(fromForecastV2)))
}

export function fetchWeather(
  query: LodQuery = {},
  runId?: string,
  signal?: AbortSignal,
): Promise<Envelope<WeatherReadingOut[]>> {
  return apiGetV2<WeatherV2Out[]>(
    `/api/v2/weather${buildQuery(lodParams(query))}`,
    runId,
    signal,
  ).then((envelope) => preserveV2Envelope(envelope, envelope.data.map(fromWeatherV2)))
}

export function fetchAlerts(
  runId?: string,
  limit?: number,
  offset?: number,
): Promise<Envelope<AlertOut[]>> {
  return apiGetV2<AlertOut[]>(`/api/v2/alerts${buildQuery({ limit, offset })}`, runId).then(
    (envelope) => preserveV2Envelope(envelope, envelope.data),
  )
}

/** The latest recorded two-partition federation demonstration. */
export function fetchFederationStatus(): Promise<Envelope<FederationStatusOut>> {
  return apiGet<Envelope<FederationStatusOut>>('/api/v1/federation/status')
}

export function fetchReports(): Promise<Envelope<FireReportOut[]>> {
  return apiGet('/api/v1/reports')
}

/** Active reports *with* their lifecycle status.
 *
 *  Reads the versioned shape (`/api/v2/reports`) rather than v1, because v1's
 *  response is deliberately unchanged — ten submission fields and nothing else —
 *  so it cannot say where a report stands. One call, status on every row; the
 *  alternative (a detail request per row) would be a request storm to render one
 *  panel. */
export function fetchReportsWithStatus(): Promise<Envelope<FireReportWithStatus[]>> {
  return apiGet('/api/v2/reports')
}

/** One report's standing, for the "what happened to my report" view. */
export function fetchReportStatus(reportId: number): Promise<Envelope<FireReportWithStatus>> {
  return apiGet(`/api/v1/reports/${reportId}`)
}

/** The lifecycle itself, so the UI can explain a status without hardcoding it. */
export function fetchReportStatuses(): Promise<
  Envelope<
    {
      status: FireReportStatus
      meaning: string
      affects_air_quality_model: boolean
    }[]
  >
> {
  return apiGet('/api/v1/reports/statuses')
}

/** NASA FIRMS detections the backend has ingested (GET /api/v1/fires),
 *  mapped to the map's own ActiveFire view model so the layers and popup
 *  don't care where the rows came from.
 *
 *  `query` is the same level-of-detail pair grid/weather take. Omit it for
 *  the whole stored set - which is what the fires layer does, since it covers
 *  all of India and the endpoint filters by H3 cell at the resolution the
 *  detections were stored at (a viewport-scoped request would have to match
 *  that resolution exactly; see backend/app/services/fires.py). */
export function fetchActiveFires(query: LodQuery = {}): Promise<Envelope<ActiveFire[]>> {
  return apiGet<Envelope<FireHotspotOut[]>>(`/api/v1/fires${buildQuery(lodParams(query))}`).then(
    (envelope) => ({
      ...envelope,
      data: envelope.data.map(activeFireFromHotspot),
    }),
  )
}

/** Recorded imagery-driven candidate scans (not confirmed pollution events). */
export function fetchHotspotCatalog(signal?: AbortSignal): Promise<Envelope<HotspotCatalogOut>> {
  return apiGet<Envelope<HotspotCatalogOut>>('/api/v1/hotspots', signal)
}

/** Persisted, spatially and temporally deduplicated potential hotspot events. */
export function fetchHotspotEvents(signal?: AbortSignal): Promise<Envelope<HotspotEventsOut>> {
  return apiGet<Envelope<HotspotEventsOut>>('/api/v1/hotspots/events', signal)
}

/** Candidate locations and evidence for one recorded detector scan. */
export function fetchHotspotScan(
  scanId: string,
  signal?: AbortSignal,
): Promise<Envelope<HotspotScanOut>> {
  return apiGet<Envelope<HotspotScanOut>>(`/api/v1/hotspots/${encodeURIComponent(scanId)}`, signal)
}

export function submitReport(payload: FireReportSubmit): Promise<Envelope<FireReportOut>> {
  return apiPost('/api/v1/reports', payload)
}

export function fetchCellDetail(
  h3Cell: string,
  resolution?: number,
  runId?: string,
): Promise<Envelope<CellDetailOut>> {
  return apiGetV2<CellDetailV2Out>(
    `/api/v2/cells/${encodeURIComponent(h3Cell)}${buildQuery({ resolution })}`,
    runId,
  ).then((envelope) => {
    const detail = envelope.data
    return preserveV2Envelope(envelope, {
      h3_cell: detail.h3_cell,
      current: detail.current === null ? null : fromCurrentV2(detail.current),
      forecasts: detail.forecasts.map(fromForecastV2),
      weather: detail.weather === null ? null : fromWeatherV2(detail.weather),
      pdi_factors: detail.pdi_factors,
      environmental: {
        run_id: envelope.run_id,
        mode: envelope.mode,
        metadata: detail.current?.metadata ?? detail.forecasts[0]?.metadata ?? null,
        exposure: detail.exposure,
        static_features: detail.static_features,
      },
    })
  })
}

export function fetchCellSatelliteContext(
  h3Cell: string,
  resolution?: number,
  signal?: AbortSignal,
  onProgress?: (loaded: number, total: number) => void,
): Promise<Envelope<CellSatelliteAnalysisOut>> {
  return apiGet<Envelope<CellSatelliteAnalysisOut>>(
    `/api/v1/cells/${encodeURIComponent(h3Cell)}/satellite-context${buildQuery({ resolution })}`,
    signal,
    onProgress,
  )
}

export function triggerCellSatelliteAnalysis(
  h3Cell: string,
  reanalyze: boolean = false,
  resolution?: number,
): Promise<Envelope<CellSatelliteAnalysisOut>> {
  return apiPost<Envelope<CellSatelliteAnalysisOut>>(
    `/api/v1/cells/${encodeURIComponent(h3Cell)}/satellite-analysis${buildQuery({ resolution })}`,
    { reanalyze },
  )
}

export function cellSatelliteThumbnailUrl(h3Cell: string): string {
  return `${API_BASE_URL}/api/v1/cells/${encodeURIComponent(h3Cell)}/satellite-thumbnail?ramp=cpcb-pm25-v1`
}
