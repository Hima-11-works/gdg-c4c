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
  CorridorCatalogEntry,
  CorridorEventBundle,
  Envelope,
  FederationStatusOut,
  FireHotspotOut,
  HotspotDetectorStatusOut,
  HotspotScanOut,
  FireReportOut,
  FireReportSubmit,
  ForecastOut,
  ForecastV2Out,
  GridCurrentV2Out,
  GridStateOut,
  IncidentCreate,
  IncidentDeliveryOut,
  IncidentEventOut,
  IncidentOut,
  InboxItemOut,
  MetaV2Out,
  ReportEvidenceOut,
  V2Envelope,
  CellDetailV2Out,
  WeatherV2Out,
  WeatherReadingOut,
} from './types'
import { activeFireFromHotspot } from './activeFires'
import type { ActiveFire } from './activeFires'
import { SIMULATOR_API_KEY, actorId } from './operatorIdentity'

// `||` (not `??`) so an empty VITE_API_BASE_URL — which a host may inject
// when auto-importing env files — still falls back, and any trailing slash is
// stripped so `${API_BASE_URL}${path}` never becomes `//api/v1/...`.
export const API_BASE_URL: string = (
  import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'
).replace(/\/+$/, '')

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

async function apiGetV2<T>(path: string, pinnedRunId?: string): Promise<V2Envelope<T>> {
  const runId = pinnedRunId ?? (await publishedRunId())
  const [pathname, queryString] = path.split('?', 2)
  const query = new URLSearchParams(queryString ?? '')
  query.set('run_id', runId)
  return apiGet<V2Envelope<T>>(`${pathname}?${query.toString()}`)
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

export function fetchGridCurrent(
  query: LodQuery = {},
  runId?: string,
): Promise<Envelope<GridStateOut[]>> {
  return apiGetV2<GridCurrentV2Out[]>(
    `/api/v2/grid/current${buildQuery(lodParams(query))}`,
    runId,
).then((envelope) => preserveV2Envelope(envelope, envelope.data.map(fromCurrentV2)))
}

export function fetchGridForecast(
  minutes: number,
  query: LodQuery = {},
  runId?: string,
): Promise<Envelope<ForecastOut[]>> {
  return apiGetV2<ForecastV2Out[]>(
    `/api/v2/grid/forecast${buildQuery({ hours: minutes / 60, ...lodParams(query) })}`,
    runId,
  ).then((envelope) => preserveV2Envelope(envelope, envelope.data.map(fromForecastV2)))
}

export function fetchWeather(
  query: LodQuery = {},
  runId?: string,
): Promise<Envelope<WeatherReadingOut[]>> {
  return apiGetV2<WeatherV2Out[]>(`/api/v2/weather${buildQuery(lodParams(query))}`, runId).then(
    (envelope) => preserveV2Envelope(envelope, envelope.data.map(fromWeatherV2)),
  )
}

export function fetchAlerts(runId?: string): Promise<Envelope<AlertOut[]>> {
  return apiGetV2<AlertOut[]>('/api/v2/alerts', runId).then((envelope) =>
    preserveV2Envelope(envelope, envelope.data),
  )
}

export function fetchReports(): Promise<Envelope<FireReportOut[]>> {
  return apiGet('/api/v1/reports')
}

/** The two-region federated-training demonstration's status
 *  (GET /api/v1/federation/status). Public and unpinned: it reports the most
 *  recent recorded demonstration run, which is not part of any published
 *  prediction run. See lib/federation.ts for what the payload may and may not
 *  be read as claiming. */
export function fetchFederationStatus(): Promise<Envelope<FederationStatusOut>> {
  return apiGet<Envelope<FederationStatusOut>>('/api/v1/federation/status')
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
  return apiGet<Envelope<FireHotspotOut[]>>(
    `/api/v1/fires${buildQuery(lodParams(query))}`,
  ).then((envelope) => ({
    ...envelope,
    data: envelope.data.map(activeFireFromHotspot),
  }))
}

/** The candidate-hotspot detector's recorded scans (GET /api/v1/hotspots).
 *  Read-only: the backend never re-runs a detector on request. */
export function fetchHotspotScans(): Promise<Envelope<HotspotDetectorStatusOut>> {
  return apiGet<Envelope<HotspotDetectorStatusOut>>('/api/v1/hotspots')
}

/** One recorded scan in full (GET /api/v1/hotspots/{scan_id}). */
export function fetchHotspotScan(scanId: string): Promise<Envelope<HotspotScanOut>> {
  return apiGet<Envelope<HotspotScanOut>>(`/api/v1/hotspots/${encodeURIComponent(scanId)}`)
}

export function submitReport(
  payload: FireReportSubmit,
): Promise<Envelope<FireReportOut>> {
  return apiPost('/api/v1/reports', payload)
}

/** How far a multipart upload has got. `fraction` is null when the browser
 *  cannot compute a total for the request, in which case the UI shows
 *  indeterminate progress rather than inventing a percentage. */
export interface UploadProgress {
  loaded: number
  total: number
  fraction: number | null
}

/**
 * Attach evidence to a report that already exists.
 *
 * This is the one call in the client that cannot use `fetch`: upload progress
 * is not observable through the fetch API, and a resident sending a photo needs
 * to see it move. XMLHttpRequest is the only browser API that reports bytes
 * sent, so it is used here and nowhere else.
 *
 * `client_report_id` inside `form` is the evidence idempotency key. Resending
 * the identical form returns `200` with the stored record; a different payload
 * under the same key is a `409`. Both are ordinary responses here - the caller
 * decides what they mean.
 */
export function submitReportEvidence(
  reportId: number,
  form: FormData,
  onProgress?: (progress: UploadProgress) => void,
): Promise<Envelope<ReportEvidenceOut>> {
  return new Promise<Envelope<ReportEvidenceOut>>((resolve, reject) => {
    const request = new XMLHttpRequest()
    request.open('POST', `${API_BASE_URL}/api/v1/reports/${reportId}/evidence`)
    request.responseType = 'json'

    if (onProgress !== undefined) {
      request.upload.onprogress = (event) => {
        onProgress({
          loaded: event.loaded,
          total: event.total,
          fraction: event.lengthComputable && event.total > 0 ? event.loaded / event.total : null,
        })
      }
    }

    request.onload = () => {
      const body = request.response as
        | (Envelope<ReportEvidenceOut> & ErrorResponseBody)
        | null
      if (request.status >= 200 && request.status < 300) {
        if (body !== null && body.data !== undefined) {
          resolve(body)
        } else {
          reject(
            new ApiError(
              request.status,
              'bad_response',
              'The server accepted the upload but returned an unreadable record.',
            ),
          )
        }
        return
      }
      reject(
        new ApiError(
          request.status,
          body?.error?.code ?? 'http_error',
          body?.error?.message ?? `Evidence upload failed with status ${request.status}`,
        ),
      )
    }
    // A transport failure, not an HTTP status: nothing about the server's
    // answer is known, so the client never invents one. Whether the bytes
    // arrived is exactly what the idempotent retry settles.
    request.onerror = () =>
      reject(
        new ApiError(0, 'network_error', 'The upload was interrupted before the server answered.'),
      )
    request.ontimeout = () =>
      reject(new ApiError(0, 'network_error', 'The upload timed out.'))
    request.onabort = () =>
      reject(new ApiError(0, 'network_error', 'The upload was cancelled.'))

    request.send(form)
  })
}

/** Read an evidence record back (GET /api/v1/reports/{id}/evidence). Rejects
 *  with code `not_found` when the report has no evidence yet. */
export function fetchReportEvidence(
  reportId: number,
): Promise<Envelope<ReportEvidenceOut>> {
  return apiGet<Envelope<ReportEvidenceOut>>(`/api/v1/reports/${reportId}/evidence`)
}

/** Absolute URL for a stored photo, from the relative `url` the evidence
 *  record carries. */
export function reportEvidencePhotoUrl(relativeUrl: string): string {
  return `${API_BASE_URL}${relativeUrl}`
}

// --- corridor pollution events (/api/v1/corridors) ---
//
// Public reads, like the rest of the simulator views. The event lookup is
// scoped to a run so the corridor view can never show an event evaluated over
// a different publication than the one on screen.

export function fetchCorridors(): Promise<Envelope<CorridorCatalogEntry[]>> {
  return apiGet<Envelope<CorridorCatalogEntry[]>>('/api/v1/corridors')
}

export interface CorridorEventQuery {
  runId?: string
  minLabels?: number
  highPollutionThresholdUgm3?: number
}

export function fetchCorridorEvent(
  corridorId: string,
  eventId: string,
  query: CorridorEventQuery = {},
): Promise<Envelope<CorridorEventBundle>> {
  const path = (id: string): string =>
    `/api/v1/corridors/${encodeURIComponent(corridorId)}/events/${encodeURIComponent(id)}${buildQuery({
      run_id: query.runId,
      min_labels: query.minLabels,
      high_pollution_threshold_ugm3: query.highPollutionThresholdUgm3,
    })}`

  return apiGet<Envelope<CorridorEventBundle>>(path(eventId)).catch((error: unknown) => {
    // The event id is a backend digest of corridor + run + horizon set, so a
    // client cannot compute it. The route *validates* the id in the path
    // instead, and when it is wrong it answers 404 naming the right one. That
    // makes a placeholder such as `latest` a complete way in: ask, be told,
    // ask again. Only that specific refusal is retried — a 404 that means
    // "this run has no results in this corridor" must still reach the caller,
    // because that is a finding about the run, not a mistyped id.
    const named = error instanceof ApiError && error.status === 404
      ? /that one is '([^']+)'/.exec(error.message)?.[1]
      : undefined
    if (named === undefined || named === eventId) throw error
    return apiGet<Envelope<CorridorEventBundle>>(path(named))
  })
}

// --- incident workflow (/api/v1/incidents) ---
//
// Reads are public. Writes need the deployment's simulator key *and* an
// `X-Actor-Id` naming a responder; the server resolves that actor's role and
// jurisdiction, so the body can never widen authority.

export interface IncidentQuery {
  status?: string
  role?: string
}

/** The incident list. Public: no key, no actor. */
export function fetchIncidents(
  query: IncidentQuery = {},
): Promise<Envelope<IncidentOut[]>> {
  return apiGet<Envelope<IncidentOut[]>>(
    `/api/v1/incidents${buildQuery({ status: query.status, role: query.role })}`,
  )
}

export function fetchIncident(id: number): Promise<Envelope<IncidentOut>> {
  return apiGet<Envelope<IncidentOut>>(`/api/v1/incidents/${id}`)
}

export function fetchIncidentHistory(id: number): Promise<Envelope<IncidentEventOut[]>> {
  return apiGet<Envelope<IncidentEventOut[]>>(`/api/v1/incidents/${id}/history`)
}

export function fetchIncidentDeliveries(id: number): Promise<Envelope<IncidentDeliveryOut[]>> {
  return apiGet<Envelope<IncidentDeliveryOut[]>>(`/api/v1/incidents/${id}/deliveries`)
}

export function fetchInbox(
  role: string,
  onlyOpen = false,
): Promise<Envelope<InboxItemOut[]>> {
  return apiGet<Envelope<InboxItemOut[]>>(
    `/api/v1/incidents/inbox${buildQuery({ role, only_open: onlyOpen ? 'true' : undefined })}`,
  )
}

/** Headers every incident write carries. Omitted entirely when unconfigured, so
 *  a read-only dashboard sends no credentials at all. */
function incidentWriteHeaders(): Record<string, string> {
  const headers: Record<string, string> = {}
  if (SIMULATOR_API_KEY !== '') headers['X-Simulator-Key'] = SIMULATOR_API_KEY
  const actor = actorId()
  if (actor !== '') headers['X-Actor-Id'] = actor
  return headers
}

async function incidentWrite(
  path: string,
  body: unknown,
  method: 'POST',
): Promise<Envelope<IncidentOut>> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', ...incidentWriteHeaders() },
    body: JSON.stringify(body),
  }).catch(() => {
    throw new ApiError(0, 'network_error', 'Could not reach the incident service.')
  })

  if (!response.ok) {
    const parsed = (await response.json().catch(() => null)) as ErrorResponseBody | null
    throw new ApiError(
      response.status,
      parsed?.error?.code ?? 'http_error',
      parsed?.error?.message ?? `Request failed with status ${response.status}`,
    )
  }
  return (await response.json()) as Envelope<IncidentOut>
}

/**
 * Open an incident from a source.
 *
 * Idempotent on the source, so a double click returns the same incident with
 * `200` rather than creating a second one; a differing severity or jurisdiction
 * under the same source is a `409 conflict` and leaves the stored record alone.
 */
export function createIncident(
  body: IncidentCreate,
): Promise<Envelope<IncidentOut>> {
  return incidentWrite('/api/v1/incidents', body, 'POST')
}

/** `reported` → `assigned`. This is also what opens the simulated inbox, and
 *  it is the only way to reach `assigned` — the transition endpoint refuses it. */
export function assignIncident(
  id: number,
  assignee: string,
): Promise<Envelope<IncidentOut>> {
  return incidentWrite(`/api/v1/incidents/${id}/assign`, { assignee }, 'POST')
}

/** Move an incident along the response sequence. */
export function transitionIncident(
  id: number,
  toStatus: string,
  note?: string,
): Promise<Envelope<IncidentOut>> {
  return incidentWrite(
    `/api/v1/incidents/${id}/transitions`,
    { to_status: toStatus, ...(note !== undefined && note !== '' ? { note } : {}) },
    'POST',
  )
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
