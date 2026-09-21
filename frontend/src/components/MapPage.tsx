import { useEffect, useRef, useState } from 'react'
import { fetchGridCurrent, fetchReports, fetchWeather } from '../lib/api'
import { useApiResource } from '../hooks/useApiResource'
import {
  ensureForecastFrame,
  useForecastFrame,
  useForecastWarming,
  warmForecastWindow,
} from '../lib/forecastFrames'
import { lodKey, lodQueryFor, weatherResolutionForLod } from '../lib/lod'
import { useMapUi } from '../state/MapUiContext'
import { AlertsPanel } from './AlertsPanel'
import { CellDetailPanel } from './CellDetailPanel'
import { FederatedStatusPill } from './FederatedStatusPill'
import { Legend } from './Legend'
import { LayerToggle } from './LayerToggle'
import { MapView } from './MapView'
import { ReportFireForm } from './ReportFireForm'
import { SearchBar } from './SearchBar'
import { StatusBanner } from './StatusBanner'
import { TimelineControl } from './TimelineControl'
import type { AsyncResource } from '../hooks/useApiResource'
import type { LodQuery } from '../lib/api'
import type { ForecastOut } from '../lib/types'

const POLL_INTERVAL_MS = 60_000

export function MapPage() {
  const { state } = useMapUi()
  const { lod, bbox, forecastMinutes } = state
  // The submit form is open/closed here so its map-centre location and the
  // reports list it refetches both come from this component's data.
  const [reportOpen, setReportOpen] = useState(false)

  // A report is filed where the user is looking: the viewport centre. The
  // backend snaps it to an H3 cell and returns that in the response.
  const reportCenter =
    bbox === null
      ? null
      : {
          latitude: (bbox.minLat + bbox.maxLat) / 2,
          longitude: (bbox.minLon + bbox.maxLon) / 2,
        }

  const viewportReady = !lod.scopedToViewport || bbox !== null
  // Padded by one cell radius (see lib/lod.ts's lodQueryFor) so cells that
  // straddle the viewport edge render instead of dropping out.
  const query: LodQuery = lodQueryFor(lod, bbox)
  const queryKey = lodKey(query)

  const currentGrid = useApiResource(() => fetchGridCurrent(query), [queryKey], {
    pollIntervalMs: POLL_INTERVAL_MS,
    enabled: viewportReady,
  })

  const isNow = forecastMinutes === 0

  // Forecast frames come from the shared store: TimelineControl prefetches
  // upcoming keyframes, and this read is served straight from cache during
  // playback — no loading flip, no re-fetch, no jitter. Polling is NOT
  // enabled here (the store handles freshness via prefetch; a 60s poll
  // during playback would churn every cached frame).
  const frame = useForecastFrame(forecastMinutes, queryKey, query, viewportReady && !isNow)
  const forecastGrid = {
    resource: {
      status: frame.status,
      data: frame.data,
      isDemo: frame.isDemo,
      message: frame.message,
    } as AsyncResource<ForecastOut[]>,
    refetch: () => ensureForecastFrame(forecastMinutes, queryKey, query),
  }

  const weatherQuery: LodQuery = { ...query, resolution: weatherResolutionForLod(lod) }
  const weather = useApiResource(() => fetchWeather(weatherQuery), [lodKey(weatherQuery)], {
    pollIntervalMs: POLL_INTERVAL_MS,
    enabled: viewportReady,
  })

  // Citizen fire/burning reports (POST/GET /api/v1/reports). Fetched once
  // here and shared by the map pins and the hex drawer, so the two can't
  // disagree; polled so a new report appears without a reload.
  const reports = useApiResource(fetchReports, [], {
    pollIntervalMs: POLL_INTERVAL_MS,
  })

  // A view change (new queryKey) invalidates the forecast cache for this
  // view: warm the current position plus the next WARM_WINDOW keyframes so
  // playback is smooth from the moment it starts. The warm-up is an explicit
  // operation (see lib/forecastFrames.ts) — it reports `warming` to the
  // banner and the timeline's play/restart buttons, and clears once every
  // frame in the window is cached.
  const queryRef = useRef(query)
  const minutesRef = useRef(forecastMinutes)
  useEffect(() => {
    queryRef.current = query
    minutesRef.current = forecastMinutes
  })
  useEffect(() => {
    if (!viewportReady) return
    warmForecastWindow(queryKey, queryRef.current, minutesRef.current)
    // Only re-warm on a view change (queryKey), not on every playback tick.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queryKey, viewportReady])

  const warming = useForecastWarming(queryKey)

  const activeBaseLayer = isNow ? currentGrid : forecastGrid
  const activeLabel = isNow
    ? 'current conditions'
    : `the +${forecastMinutes >= 60 ? `${Math.floor(forecastMinutes / 60)}h ` : ''}${forecastMinutes % 60 ? `${forecastMinutes % 60}m ` : ''}forecast`.trim()

  return (
    <div className="map-page">
      <div className="banner-stack">
        <StatusBanner
          label={activeLabel}
          resource={activeBaseLayer.resource}
          onRetry={activeBaseLayer.refetch}
          warming={warming}
        />
      </div>

      <MapView
        currentGrid={currentGrid.resource}
        forecastGrid={forecastGrid.resource}
        weather={weather.resource}
        citizenReports={reports.resource}
      />

      <div className="overlay overlay-top-left">
        {reportCenter !== null && (
          <button
            type="button"
            className="panel report-open"
            onClick={() => setReportOpen(true)}
          >
            Report a fire
          </button>
        )}
        {reportOpen && reportCenter !== null && (
          <ReportFireForm
            latitude={reportCenter.latitude}
            longitude={reportCenter.longitude}
            onClose={() => setReportOpen(false)}
            onSubmitted={reports.refetch}
          />
        )}
      </div>

      <div className="overlay overlay-top-right">
        <SearchBar />
        <FederatedStatusPill />
        <AlertsPanel />
        <Legend />
      </div>

      <div className="overlay overlay-bottom-left">
        <LayerToggle />
      </div>

      <div className="overlay overlay-bottom-center">
        <TimelineControl />
      </div>

      <CellDetailPanel citizenReports={reports.resource} />
    </div>
  )
}
