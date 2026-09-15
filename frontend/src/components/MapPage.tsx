import { fetchGridCurrent, fetchGridForecast, fetchWeather } from '../lib/api'
import { useApiResource } from '../hooks/useApiResource'
import { INDIA_BBOX, weatherResolutionForLod } from '../lib/lod'
import { useMapUi } from '../state/MapUiContext'
import { AlertsPanel } from './AlertsPanel'
import { CellDetailPanel } from './CellDetailPanel'
import { Legend } from './Legend'
import { LayerToggle } from './LayerToggle'
import { MapView } from './MapView'
import { StatusBanner } from './StatusBanner'
import { TimelineControl } from './TimelineControl'
import type { LodQuery } from '../lib/api'
import type { ForecastHorizonHours } from '../lib/types'

const POLL_INTERVAL_MS = 60_000

// A stable primitive key for useApiResource's dependency array — a fresh
// bbox object every render would never compare equal, and rounding to
// ~1km also means a sub-pixel pan doesn't retrigger a fetch on its own
// (MapView's own debounce already limits how often this can even change).
function lodKey(query: LodQuery): string {
  if (!query.bbox) return `${query.resolution ?? 'default'}:nationwide`
  const round = (n: number) => Math.round(n * 100) / 100
  const { minLat, minLon, maxLat, maxLon } = query.bbox
  return `${query.resolution ?? 'default'}:${round(minLat)},${round(minLon)},${round(maxLat)},${round(maxLon)}`
}

export function MapPage() {
  const { state } = useMapUi()
  const { lod, bbox } = state

  // Country tier always requests INDIA_BBOX explicitly rather than
  // omitting bbox — the backend only applies `resolution` together with
  // a bbox (see app.services.grid's docstring); omitting it means
  // "unfiltered", not "nationwide". State/local tiers use the real
  // viewport, which MapView hasn't reported yet for the first instant
  // after a tier change — skip fetching rather than ask for a
  // fine-resolution read with no bbox.
  const viewportReady = !lod.scopedToViewport || bbox !== null
  const query: LodQuery = {
    resolution: lod.resolution,
    bbox: lod.scopedToViewport ? (bbox ?? undefined) : INDIA_BBOX,
  }
  const queryKey = lodKey(query)

  const currentGrid = useApiResource(() => fetchGridCurrent(query), [queryKey], {
    pollIntervalMs: POLL_INTERVAL_MS,
    enabled: viewportReady,
  })

  const forecastHours: ForecastHorizonHours | null = state.horizon === 'now' ? null : state.horizon
  const forecastGrid = useApiResource(
    () => fetchGridForecast(forecastHours ?? 1, query),
    [forecastHours, queryKey],
    { pollIntervalMs: POLL_INTERVAL_MS, enabled: viewportReady && forecastHours !== null },
  )

  // A coarser resolution than the PM2.5 grid at the country tier — see
  // lib/lod.ts's weatherResolutionForLod: the map only ever renders a
  // thinned, sparse subset of these points, so fetching them at the
  // grid's own (much finer) country-tier resolution would be wasted
  // payload. Its own query key: a resolution-only change must still
  // trigger a re-fetch even though `bbox` didn't change.
  const weatherQuery: LodQuery = { ...query, resolution: weatherResolutionForLod(lod) }
  const weather = useApiResource(() => fetchWeather(weatherQuery), [lodKey(weatherQuery)], {
    pollIntervalMs: POLL_INTERVAL_MS,
    enabled: viewportReady,
  })

  const activeBaseLayer = state.horizon === 'now' ? currentGrid : forecastGrid
  const activeLabel =
    state.horizon === 'now' ? 'current conditions' : `the +${state.horizon}h forecast`

  return (
    <div className="map-page">
      <div className="banner-stack">
        <StatusBanner
          label={activeLabel}
          resource={activeBaseLayer.resource}
          onRetry={activeBaseLayer.refetch}
        />
      </div>

      <MapView
        currentGrid={currentGrid.resource}
        forecastGrid={forecastGrid.resource}
        weather={weather.resource}
      />

      <div className="overlay overlay-top-left">
        <Legend />
        <LayerToggle />
      </div>

      <div className="overlay overlay-top-right">
        <AlertsPanel />
      </div>

      <div className="overlay overlay-bottom-center">
        <TimelineControl />
      </div>

      <CellDetailPanel />
    </div>
  )
}
