import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  fetchActiveFires,
  fetchGridCurrent,
  fetchPublishedMeta,
  fetchReportsWithStatus,
  fetchWeather,
} from '../lib/api'
import { useApiResource } from '../hooks/useApiResource'
import {
  ensureForecastFrame,
  cancelForecastRequests,
  useForecastFrame,
  useForecastWarming,
  warmForecastWindow,
} from '../lib/forecastFrames'
import { lodForZoom, lodKey, lodQueryFor, weatherResolutionForLod } from '../lib/lod'
import { useMapUi } from '../state/MapUiContext'
import { AlertsPanel } from './AlertsPanel'
import { CellDetailPanel } from './CellDetailPanel'
import { FederatedStatusPill } from './FederatedStatusPill'
import { Legend } from './Legend'
import { LayerToggle } from './LayerToggle'
import { MapView } from './MapView'
import { ReportFireForm } from './ReportFireForm'
import { ScopeChip } from './ScopeChip'
import { SearchBar } from './SearchBar'
import { StatusBanner } from './StatusBanner'
import { ProvenanceBanner } from './ProvenanceBanner'
import { PhotoReviewPanel } from './PhotoReviewPanel'
import { HotspotEvidencePanel } from './HotspotEvidencePanel'
import { TimelineControl } from './TimelineControl'
import type { AsyncResource } from '../hooks/useApiResource'
import type { LodQuery } from '../lib/api'
import type { ForecastOut } from '../lib/types'
import { findLocalPollutionHotspots } from '../lib/localHotspots'

const POLL_INTERVAL_MS = 60_000
const FALLBACK_SUPPORTED_HOURS = [1, 3, 6]
// FIRMS near-real-time detections arrive on the order of an hour, and the
// backend ingests them on its own schedule, so the app's 60s cadence would
// only re-read unchanged rows. This refreshes far more slowly.
const FIRMS_POLL_INTERVAL_MS = 10 * 60 * 1000

export function MapPage() {
  const { state, dispatch } = useMapUi()
  const { lod, bbox, forecastMinutes } = state
  // The submit form is open/closed here so its map-centre location and the
  // reports list it refetches both come from this component's data.
  const [reportOpen, setReportOpen] = useState(false)
  const [photoReviewOpen, setPhotoReviewOpen] = useState(false)
  const [viewportMoving, setViewportMoving] = useState(false)

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
  const viewKey = lodKey(query)
  const publishedMeta = useApiResource(fetchPublishedMeta, [], {
    pollIntervalMs: 5 * 60 * 1000,
  })
  const supportedHours =
    publishedMeta.resource.status === 'success'
      ? publishedMeta.resource.data.supported_horizons_hours
      : FALLBACK_SUPPORTED_HOURS
  const publishedRunId =
    publishedMeta.resource.status === 'success'
      ? publishedMeta.resource.data.latest_run_id
      : undefined
  const demoMode =
    publishedMeta.resource.status === 'success' &&
    publishedMeta.resource.data.data_mode === 'demo' &&
    publishedMeta.resource.data.is_demo

  const detailedGrid = useApiResource(
    (signal) => fetchGridCurrent(query, publishedRunId, signal),
    [viewKey, publishedRunId],
    {
      pollIntervalMs: POLL_INTERVAL_MS,
      enabled:
        !viewportMoving &&
        viewportReady &&
        publishedRunId !== undefined &&
        (!demoMode || lod.level >= 2),
    },
  )
  // Keep a coarse India grid available as a visual fallback when detailed
  // cells have no estimate. Fine map cells inherit only the nearest parent
  // estimate; the map marks these values as generalized and opens the source
  // parent when clicked. This runs only in demo mode, where that overview is
  // explicitly illustrative.
  const overviewQuery = lodQueryFor(lodForZoom(0), null)
  const overviewGrid = useApiResource(
    () => fetchGridCurrent(overviewQuery, publishedRunId),
    [lodKey(overviewQuery), publishedRunId],
    {
      pollIntervalMs: POLL_INTERVAL_MS,
      enabled: demoMode && publishedRunId !== undefined,
    },
  )
  const parentLod = { ...lod, tier: 'state' as const, resolution: 4, scopedToViewport: true }
  const parentQuery = lodQueryFor(parentLod, bbox)
  const parentGrid = useApiResource(
    (signal) => fetchGridCurrent(parentQuery, publishedRunId, signal),
    [lodKey(parentQuery), publishedRunId],
    {
      pollIntervalMs: POLL_INTERVAL_MS,
      enabled:
        !viewportMoving &&
        demoMode &&
        lod.level >= 3 &&
        viewportReady &&
        publishedRunId !== undefined,
    },
  )
  // The nationwide query is the Resolution 1 view itself, so keep it as the
  // source at that level and reuse it as fallback after zooming in.
  const currentGrid = lod.level === 1 && demoMode ? overviewGrid : detailedGrid
  const localHotspots = useMemo(
    () =>
      lod.level >= 3 && forecastMinutes === 0 && currentGrid.resource.status === 'success'
        ? findLocalPollutionHotspots(currentGrid.resource.data, currentGrid.resource.isDemo)
        : [],
    [currentGrid.resource, forecastMinutes, lod.level],
  )
  const queryKey = `${viewKey}:${publishedRunId ?? 'pending'}:${supportedHours.join(',')}`

  const isNow = forecastMinutes === 0

  // Forecast frames come from the shared store: TimelineControl prefetches
  // upcoming keyframes, and this read is served straight from cache during
  // playback — no loading flip, no re-fetch, no jitter. Polling is NOT
  // enabled here (the store handles freshness via prefetch; a 60s poll
  // during playback would churn every cached frame).
  const frame = useForecastFrame(
    forecastMinutes,
    queryKey,
    query,
    !viewportMoving && viewportReady && !isNow && publishedRunId !== undefined,
    publishedRunId,
  )
  const forecastGrid = {
    resource: {
      status: frame.status,
      data: frame.data,
      isDemo: frame.isDemo,
      runId: frame.runId,
      mode: frame.mode,
      generatedAt: frame.generatedAt,
      message: frame.message,
    } as AsyncResource<ForecastOut[]>,
    refetch: () => ensureForecastFrame(forecastMinutes, queryKey, query, publishedRunId),
  }
  const overviewForecastKey = `${lodKey(overviewQuery)}:${publishedRunId ?? 'pending'}:${supportedHours.join(',')}`
  const overviewForecastFrame = useForecastFrame(
    forecastMinutes,
    overviewForecastKey,
    overviewQuery,
    demoMode && !isNow && publishedRunId !== undefined,
    publishedRunId,
  )
  const overviewForecastGrid = {
    status: overviewForecastFrame.status,
    data: overviewForecastFrame.data,
    isDemo: overviewForecastFrame.isDemo,
    runId: overviewForecastFrame.runId,
    mode: overviewForecastFrame.mode,
    generatedAt: overviewForecastFrame.generatedAt,
    message: overviewForecastFrame.message,
  } as AsyncResource<ForecastOut[]>
  const parentForecastKey = `${lodKey(parentQuery)}:${publishedRunId ?? 'pending'}:${supportedHours.join(',')}`
  const parentForecastFrame = useForecastFrame(
    forecastMinutes,
    parentForecastKey,
    parentQuery,
    !viewportMoving &&
      demoMode &&
      !isNow &&
      lod.level >= 3 &&
      viewportReady &&
      publishedRunId !== undefined,
    publishedRunId,
  )
  const parentForecastGrid = {
    status: parentForecastFrame.status,
    data: parentForecastFrame.data,
    isDemo: parentForecastFrame.isDemo,
    runId: parentForecastFrame.runId,
    mode: parentForecastFrame.mode,
    generatedAt: parentForecastFrame.generatedAt,
    message: parentForecastFrame.message,
  } as AsyncResource<ForecastOut[]>

  // Abort frame and warm-up requests for both viewport-bound queries when
  // moving starts or the component leaves this view.
  useEffect(
    () => () => {
      cancelForecastRequests(queryKey)
      cancelForecastRequests(parentForecastKey)
    },
    [queryKey, parentForecastKey],
  )

  const weatherQuery: LodQuery = { ...query, resolution: weatherResolutionForLod(lod) }
  const weather = useApiResource(
    (signal) => fetchWeather(weatherQuery, publishedRunId, signal),
    [lodKey(weatherQuery), publishedRunId],
    {
      pollIntervalMs: POLL_INTERVAL_MS,
      enabled: !viewportMoving && viewportReady && publishedRunId !== undefined,
    },
  )

  // Citizen fire/burning reports (POST /api/v1/reports, read back from
  // GET /api/v2/reports). Fetched once here and shared by the map pins and the
  // hex drawer, so the two can't disagree; polled so a new report - or a
  // reviewer's decision about one - appears without a reload.
  //
  // The v2 read rather than v1: v1's response is deliberately unchanged (ten
  // submission fields), so it cannot say whether a report has been corroborated
  // or is still affecting the model. The layer shows that, because a pin that
  // looks identical whether or not the report counts is the misreading F1 exists
  // to prevent. The row type is a superset of the v1 one, so every existing
  // consumer keeps working.
  const reports = useApiResource(fetchReportsWithStatus, [], {
    pollIntervalMs: POLL_INTERVAL_MS,
  })

  // NASA FIRMS active fires, read from our own backend (GET /api/v1/fires)
  // instead of NASA's public CSV. The backend holds the FIRMS credentials,
  // does the parsing and owns the caching; the browser needs no key and no
  // CSV parser, and the map and the pipeline see the same detections.
  //
  // Only fetched while the layer is switched on - enabling the toggle flips
  // `enabled` and the hook fetches immediately. No bbox is sent: this layer
  // covers all of India, and the endpoint clips by H3 cell at the resolution
  // the detections were stored at, which a viewport-scoped request wouldn't
  // match (see backend/app/services/fires.py).
  const firesNeeded =
    state.showActiveFires || state.showHotspotCandidates || state.selectedCell !== null
  const activeFires = useApiResource(() => fetchActiveFires(), [firesNeeded], {
    pollIntervalMs: FIRMS_POLL_INTERVAL_MS,
    enabled: firesNeeded,
  })

  const cancelDetailedGrid = detailedGrid.cancel
  const cancelParentGrid = parentGrid.cancel
  const cancelWeather = weather.cancel
  const handleViewportMoveStart = useCallback(() => {
    setViewportMoving(true)
    cancelDetailedGrid()
    cancelParentGrid()
    cancelWeather()
    cancelForecastRequests(queryKey)
    cancelForecastRequests(parentForecastKey)
  }, [cancelDetailedGrid, cancelParentGrid, cancelWeather, queryKey, parentForecastKey])
  const handleViewportSettled = useCallback(() => setViewportMoving(false), [])

  // A view change (new queryKey) invalidates the forecast cache for this
  // view: warm the current position plus the next WARM_WINDOW keyframes so
  // playback is smooth from the moment it starts. The warm-up is an explicit
  // operation (see lib/forecastFrames.ts) — it reports `warming` to the
  // banner and the timeline's play/restart buttons, and clears once every
  // frame in the window is cached.
  //
  // It runs only once the user has actually asked for a forecast, i.e.
  // forecastMinutes > 0, which is exactly when they have scrubbed or pressed
  // play. On first load this fetched WARM_WINDOW + 1 frames the user never
  // looked at, alongside the one country-tier current frame that is actually
  // displayed — so opening the map cost ten requests to draw one. Deferring it
  // to engagement makes the first paint a single request, and the detail
  // arrives when it is asked for.
  const queryRef = useRef(query)
  const minutesRef = useRef(forecastMinutes)
  useEffect(() => {
    queryRef.current = query
    minutesRef.current = forecastMinutes
  })
  useEffect(() => {
    if (viewportMoving || !viewportReady || publishedRunId === undefined) return
    if (isNow) return
    warmForecastWindow(
      queryKey,
      queryRef.current,
      minutesRef.current,
      supportedHours,
      publishedRunId,
    )
    // Re-warms on a view change or when the user first engages, not on every
    // playback tick.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queryKey, viewportReady, viewportMoving, supportedHours, publishedRunId, isNow])

  const warming = useForecastWarming(queryKey)
  const isInterpolated = forecastMinutes > 0 && !supportedHours.includes(forecastMinutes / 60)

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
          interpolated={!isNow && isInterpolated}
        />
        <ProvenanceBanner meta={publishedMeta.resource} />
      </div>

      <MapView
        currentGrid={currentGrid.resource}
        forecastGrid={forecastGrid.resource}
        overviewGrid={overviewGrid.resource}
        parentGrid={parentGrid.resource}
        overviewForecastGrid={overviewForecastGrid}
        parentForecastGrid={parentForecastGrid}
        weather={weather.resource}
        citizenReports={reports.resource}
        activeFires={activeFires.resource}
        localHotspots={localHotspots}
        onViewportMoveStart={handleViewportMoveStart}
        onViewportSettled={handleViewportSettled}
      />

      <div className="overlay overlay-top-left">
        <Legend />
        <div className="panel resolution-indicator" role="status" aria-live="polite">
          Resolution {lod.level}
        </div>
        {reportCenter !== null && (
          <button type="button" className="panel report-open" onClick={() => setReportOpen(true)}>
            Report a fire
          </button>
        )}
        <button
          type="button"
          className="panel report-open"
          onClick={() => setPhotoReviewOpen(true)}
          aria-haspopup="dialog"
        >
          Review citizen photos
        </button>
        <button
          type="button"
          className={`panel report-open hotspot-toggle-btn ${state.hotspotPanelOpen ? 'active' : ''}`}
          onClick={() => {
            if (!state.showHotspotCandidates) dispatch({ type: 'TOGGLE_HOTSPOT_CANDIDATES' })
            dispatch({ type: 'TOGGLE_HOTSPOT_PANEL' })
          }}
          aria-expanded={state.hotspotPanelOpen}
          aria-controls="hotspot-evidence-panel"
          aria-label={
            state.hotspotPanelOpen ? 'Hide fire candidate evidence' : 'Show fire candidate evidence'
          }
          title={
            state.hotspotPanelOpen ? 'Hide fire candidate evidence' : 'Show fire candidate evidence'
          }
        >
          {state.hotspotPanelOpen ? 'Hide fire candidate evidence' : 'Show fire candidate evidence'}
        </button>
        {reportOpen && reportCenter !== null && (
          <ReportFireForm
            latitude={reportCenter.latitude}
            longitude={reportCenter.longitude}
            onClose={() => setReportOpen(false)}
            onSubmitted={reports.refetch}
          />
        )}
      </div>

      {photoReviewOpen && (
        <PhotoReviewPanel onClose={() => setPhotoReviewOpen(false)} onReviewed={reports.refetch} />
      )}

      <div className="overlay overlay-top-right">
        <div className="top-right-row">
          <SearchBar />
          <AlertsPanel publishedRunId={publishedRunId} />
        </div>
        <FederatedStatusPill />
      </div>

      <div className="overlay overlay-bottom-left">
        <LayerToggle />
      </div>

      <div className="overlay overlay-bottom-center">
        <ScopeChip />
        <TimelineControl publishedRunId={publishedRunId} supportedHours={supportedHours} />
      </div>

      <CellDetailPanel
        publishedRunId={publishedRunId}
        citizenReports={reports.resource}
        activeFires={activeFires.resource}
      />
      <HotspotEvidencePanel
        resource={activeFires.resource}
        localHotspots={localHotspots}
        gridResource={currentGrid.resource}
        resolutionLevel={lod.level}
        forecastMinutes={forecastMinutes}
      />
    </div>
  )
}
