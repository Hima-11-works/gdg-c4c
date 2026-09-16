import { fetchGridCurrent, fetchWeather } from '../lib/api'
import { useApiResource } from '../hooks/useApiResource'
import { ensureForecastFrame, useForecastFrame } from '../lib/forecastFrames'
import { INDIA_BBOX, lodKey, weatherResolutionForLod } from '../lib/lod'
import { useMapUi } from '../state/MapUiContext'
import { AlertsPanel } from './AlertsPanel'
import { CellDetailPanel } from './CellDetailPanel'
import { Legend } from './Legend'
import { LayerToggle } from './LayerToggle'
import { MapView } from './MapView'
import { StatusBanner } from './StatusBanner'
import { TimelineControl } from './TimelineControl'
import type { AsyncResource } from '../hooks/useApiResource'
import type { LodQuery } from '../lib/api'
import type { ForecastOut } from '../lib/types'

const POLL_INTERVAL_MS = 60_000

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
