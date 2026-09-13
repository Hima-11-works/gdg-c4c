import { fetchGridCurrent, fetchGridForecast, fetchWeather } from '../lib/api'
import { useApiResource } from '../hooks/useApiResource'
import { useMapUi } from '../state/MapUiContext'
import { AlertsPanel } from './AlertsPanel'
import { CellDetailPanel } from './CellDetailPanel'
import { Legend } from './Legend'
import { LayerToggle } from './LayerToggle'
import { MapView } from './MapView'
import { StatusBanner } from './StatusBanner'
import { TimelineControl } from './TimelineControl'
import type { ForecastHorizonHours } from '../lib/types'

const POLL_INTERVAL_MS = 60_000

export function MapPage() {
  const { state } = useMapUi()

  const currentGrid = useApiResource(fetchGridCurrent, [], { pollIntervalMs: POLL_INTERVAL_MS })

  const forecastHours: ForecastHorizonHours | null = state.horizon === 'now' ? null : state.horizon
  const forecastGrid = useApiResource(
    () => fetchGridForecast(forecastHours ?? 1),
    [forecastHours],
    { pollIntervalMs: POLL_INTERVAL_MS, enabled: forecastHours !== null },
  )

  const weather = useApiResource(fetchWeather, [], { pollIntervalMs: POLL_INTERVAL_MS })

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
