import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  fetchActiveFires,
  fetchHotspotScan,
  fetchHotspotScans,
  fetchGridCurrent,
  fetchPublishedMeta,
  fetchReports,
  fetchWeather,
} from '../lib/api'
import { useApiResource } from '../hooks/useApiResource'
import {
  ensureForecastFrame,
  useForecastFrame,
  useForecastWarming,
  warmForecastWindow,
} from '../lib/forecastFrames'
import { lodKey, lodQueryFor, weatherResolutionForLod } from '../lib/lod'
import { runFactsFromGrid, staleness } from '../lib/runFacts'
import { CorridorPanel } from './CorridorPanel'
import { HotspotEvidencePanel } from './HotspotEvidencePanel'
import { useMapUi } from '../state/MapUiContext'
import type { CorridorCatalogEntry, CorridorEventBundle, HotspotScanOut } from '../lib/types'
import { AlertsPanel } from './AlertsPanel'
import { CellDetailPanel } from './CellDetailPanel'
import { DataQualityNotice } from './DataQualityNotice'
import { FederatedStatusPill } from './FederatedStatusPill'
import { Legend } from './Legend'
import { LayerToggle } from './LayerToggle'
import { MapView } from './MapView'
import { ReportFireForm } from './ReportFireForm'
import { RunStatusPanel } from './RunStatusPanel'
import { ScopeChip } from './ScopeChip'
import { SearchBar } from './SearchBar'
import { StatusBanner } from './StatusBanner'
import { TimelineControl } from './TimelineControl'
import type { AsyncResource } from '../hooks/useApiResource'
import type { LodQuery } from '../lib/api'
import type { ForecastOut } from '../lib/types'

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
  // The corridor selected in the corridor view, and the event it loaded. Both
  // live here rather than inside the panel because the map draws them: the
  // selection as an illustrative axis, and the event's own cells as the
  // markers. An event that fails to load clears the cells, so the map cannot
  // keep showing an evaluation the panel has just said does not exist.
  const [corridor, setCorridor] = useState<CorridorCatalogEntry | null>(null)
  const [corridorEvent, setCorridorEvent] = useState<CorridorEventBundle | null>(null)

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

  const currentGrid = useApiResource(
    () => fetchGridCurrent(query, publishedRunId),
    [viewKey, publishedRunId],
    {
      pollIntervalMs: POLL_INTERVAL_MS,
      enabled: viewportReady && publishedRunId !== undefined,
    },
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
    viewportReady && !isNow && publishedRunId !== undefined,
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

  const weatherQuery: LodQuery = { ...query, resolution: weatherResolutionForLod(lod) }
  const weather = useApiResource(
    () => fetchWeather(weatherQuery, publishedRunId),
    [lodKey(weatherQuery), publishedRunId],
    {
      pollIntervalMs: POLL_INTERVAL_MS,
      enabled: viewportReady && publishedRunId !== undefined,
    },
  )

  // Citizen fire/burning reports (POST/GET /api/v1/reports). Fetched once
  // here and shared by the map pins and the hex drawer, so the two can't
  // disagree; polled so a new report appears without a reload.
  const reports = useApiResource(fetchReports, [], {
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
  // The candidate layer and the detection layer are drawn from these same
  // records, so one fetch feeds both; either toggle is enough to need it.
  const firesNeeded = state.showActiveFires || state.showHotspotCandidates
  const activeFires = useApiResource(() => fetchActiveFires(), [firesNeeded], {
    pollIntervalMs: FIRMS_POLL_INTERVAL_MS,
    enabled: firesNeeded,
  })

  // The candidate-hotspot detector's recorded scans. The index lists them; a
  // scan is only opened on demand, because the map needs its candidates and the
  // panel needs the full record.
  const hotspots = useApiResource(() => fetchHotspotScans(), [state.showHotspotCandidates], {
    enabled: state.showHotspotCandidates || state.hotspotPanelOpen,
  })
  const [hotspotScan, setHotspotScan] = useState<{
    scanId: string
    data: HotspotScanOut
  } | null>(null)
  const [hotspotScanError, setHotspotScanError] = useState<string | null>(null)

  const openHotspotScan = useCallback(
    async (scanId: string) => {
      setHotspotScanError(null)
      try {
        const envelope = await fetchHotspotScan(scanId)
        setHotspotScan({ scanId, data: envelope.data })
      } catch (error) {
        setHotspotScan(null)
        setHotspotScanError(error instanceof Error ? error.message : String(error))
      }
    },
    [],
  )

  const hotspotCandidates = hotspotScan?.data.candidates ?? []

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
    if (!viewportReady || publishedRunId === undefined) return
    warmForecastWindow(queryKey, queryRef.current, minutesRef.current, supportedHours, publishedRunId)
    // Only re-warm on a view change (queryKey), not on every playback tick.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queryKey, viewportReady, supportedHours, publishedRunId])

  const warming = useForecastWarming(queryKey)
  const isInterpolated = forecastMinutes > 0 && !supportedHours.includes(forecastMinutes / 60)

  const activeBaseLayer = isNow ? currentGrid : forecastGrid
  const activeLabel = isNow
    ? 'current conditions'
    : `the +${forecastMinutes >= 60 ? `${Math.floor(forecastMinutes / 60)}h ` : ''}${forecastMinutes % 60 ? `${forecastMinutes % 60}m ` : ''}forecast`.trim()

  // A forecast frame from a different published run than the one the status
  // panel describes would put two runs' facts and two runs' values on one
  // screen. The cache key already contains the run id, so this should never
  // fire — but if it ever does, the honest response is to show neither rather
  // than a map painted from one run beside a panel describing another.
  const currentRunId = currentGrid.resource.status === 'success' ? currentGrid.resource.runId : undefined
  const frameRunId = forecastGrid.resource.status === 'success' ? forecastGrid.resource.runId : undefined
  const runIdMismatch =
    !isNow && currentRunId !== undefined && frameRunId !== undefined && currentRunId !== frameRunId
  const shownBaseLayer = runIdMismatch
    ? { resource: { status: 'loading' as const }, refetch: forecastGrid.refetch }
    : activeBaseLayer

  // Run-level facts come from the current-conditions read, which is always
  // fetched and always pinned to the same published run as the forecast
  // frames — so the status panel describes the run the map is drawing even
  // while a forecast horizon is playing.
  const runFacts = useMemo(() => {
    const current = currentGrid.resource
    return runFactsFromGrid(current.status === 'success' ? current.data : [], {
      runId: current.status === 'success' ? current.runId : undefined,
      mode: current.status === 'success' ? current.mode : undefined,
      generatedAt: current.status === 'success' ? current.generatedAt : undefined,
      coverage: current.status === 'success' ? current.coverage : undefined,
      attribution: current.status === 'success' ? current.attribution : undefined,
    })
  }, [currentGrid.resource])
  const runStaleness = useMemo(() => staleness(runFacts), [runFacts])
  const activeHasData =
    shownBaseLayer.resource.status === 'success' && shownBaseLayer.resource.data.length > 0

  return (
    <div className="map-page">
      <div className="banner-stack">
        {runIdMismatch && (
          <div className="banner banner-stale" role="alert">
            <strong>Two different runs.</strong> The forecast frame on screen is from {frameRunId}{' '}
            while this panel describes {currentRunId}. Nothing is drawn until they agree.
          </div>
        )}
        <StatusBanner
          label={activeLabel}
          resource={shownBaseLayer.resource}
          onRetry={shownBaseLayer.refetch}
          warming={warming}
          interpolated={!isNow && isInterpolated}
        />
        <DataQualityNotice
          facts={runFacts}
          staleness={runStaleness}
          metricIsExposure={state.mapMetric === 'exposure'}
          hasData={activeHasData}
        />
      </div>

      <MapView
        currentGrid={currentGrid.resource}
        forecastGrid={runIdMismatch ? { status: 'loading' } : forecastGrid.resource}
        weather={weather.resource}
        citizenReports={reports.resource}
        activeFires={activeFires.resource}
        corridor={corridor}
        corridorCells={corridorEvent?.event.cells ?? null}
        hotspotCandidates={hotspotCandidates}
      />

      <div className="overlay overlay-top-left">
        <Legend />
        <div className="overlay-buttons">
          <button
            type="button"
            className="panel report-open"
            onClick={() => dispatch({ type: 'TOGGLE_CORRIDOR_PANEL' })}
          >
            Corridor event…
          </button>
          <button
            type="button"
            className="panel report-open"
            title="Imagery-derived fire candidates and the evidence behind them. A candidate is not a confirmed fire, and this is not measured PM2.5."
            onClick={() => {
              // Turning the layer on with the panel, because a candidate list
              // whose markers are all hidden reads as though nothing was found.
              if (!state.showHotspotCandidates) {
                dispatch({ type: 'TOGGLE_HOTSPOT_CANDIDATES' })
              }
              dispatch({ type: 'TOGGLE_HOTSPOT_PANEL' })
            }}
          >
            Fire candidates…
          </button>
          {reportCenter !== null && (
            <button
              type="button"
              className="panel report-open"
              onClick={() => setReportOpen(true)}
            >
              Report a fire
            </button>
          )}
        </div>
      </div>

      <div className="overlay overlay-top-right">
        <div className="top-right-row">
          <SearchBar />
          <AlertsPanel publishedRunId={publishedRunId} />
        </div>
        <FederatedStatusPill />
        <RunStatusPanel
          resource={currentGrid.resource}
          facts={runFacts}
          staleness={runStaleness}
        />
      </div>

      <div className="overlay overlay-bottom-left">
        <LayerToggle />
      </div>

      <div className="overlay overlay-bottom-center">
        <ScopeChip />
        <TimelineControl
          publishedRunId={publishedRunId}
          supportedHours={supportedHours}
        />
      </div>

      <CellDetailPanel publishedRunId={publishedRunId} citizenReports={reports.resource} />

      <HotspotEvidencePanel
        index={hotspots.resource}
        scans={hotspotScan === null ? {} : { [hotspotScan.scanId]: hotspotScan.data }}
        onSelectScan={openHotspotScan}
        scanError={hotspotScanError}
      />

      <CorridorPanel
        runId={publishedRunId}
        onSelect={(entry) => {
          setCorridor(entry)
          setCorridorEvent(null)
        }}
        onEventLoaded={(bundle) => setCorridorEvent(bundle)}
      />

      {/* The report form is a modal, and deliberately NOT inside an overlay:
          every `.overlay` establishes its own stacking context, so a modal
          rendered inside one could not rise above its sibling overlays and
          their panels would intercept its clicks. As a direct child of the
          page it covers all of them. */}
      {reportOpen && reportCenter !== null && (
        <div
          className="report-modal-backdrop"
          role="dialog"
          aria-modal="true"
          aria-label="Report a fire"
        >
          <ReportFireForm
            latitude={reportCenter.latitude}
            longitude={reportCenter.longitude}
            onClose={() => setReportOpen(false)}
            onSubmitted={reports.refetch}
          />
        </div>
      )}
    </div>
  )
}
