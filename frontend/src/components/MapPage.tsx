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
  const { lod, bbox, forecastMinutes } = state

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

  const isNow = forecastMinutes === 0
  const forecastGrid = useApiResource(
    () => fetchGridForecast(forecastMinutes, query),
    [forecastMinutes, queryKey],
    { pollIntervalMs: POLL_INTERVAL_MS, enabled: viewportReady && !isNow },
  )

  const weatherQuery: LodQuery = { ...query, resolution: weatherResolutionForLod(lod) }
  const weather = useApiResource(() => fetchWeather(weatherQuery), [lodKey(weatherQuery)], {
    pollIntervalMs: POLL_INTERVAL_MS,
    enabled: viewportReady,
  })

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
