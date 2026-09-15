import { NO_DATA_COLOR, PDI_COLOR_SCALE, PM25_COLOR_SCALE } from '../lib/colorScales'
import { useMapUi } from '../state/MapUiContext'

export function Legend() {
  const { state } = useMapUi()

  return (
    <div className="panel legend">
      <section>
        <h3>PM2.5 (µg/m³)</h3>
        {PM25_COLOR_SCALE.map((stop) => (
          <div className="legend-row" key={stop.value}>
            <span className="swatch" style={{ backgroundColor: stop.color }} />
            {stop.label}
          </div>
        ))}
      </section>

      {state.showPdi && (
        <section>
          <h3>PDI (heuristic, not a measurement)</h3>
          {PDI_COLOR_SCALE.map((stop) => (
            <div className="legend-row" key={stop.value}>
              <span className="swatch" style={{ backgroundColor: stop.color }} />
              {stop.label}
            </div>
          ))}
        </section>
      )}

      <section>
        <div className="legend-row">
          <span className="swatch" style={{ backgroundColor: NO_DATA_COLOR }} />
          No estimate
        </div>
        <div className="legend-row">
          <span aria-hidden="true">↑</span> Wind direction (arrow points downwind)
        </div>
      </section>

      <section>
        <p className="muted legend-note">
          Zoomed out: one marker per city. Zoom in on a city to see per-hex detail, or click any
          marker/hex for its full readings.
        </p>
      </section>
    </div>
  )
}
