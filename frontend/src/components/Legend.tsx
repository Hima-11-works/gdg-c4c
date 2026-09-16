import { NO_DATA_COLOR, PDI_COLOR_SCALE, PM25_COLOR_SCALE } from '../lib/colorScales'
import { BASEMAP, WIND } from '../lib/mapTheme'
import { PDI_LABEL, PDI_TOOLTIP } from '../lib/format'
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
          <h3 title={PDI_TOOLTIP}>{PDI_LABEL}</h3>
          <p className="muted legend-note">{PDI_TOOLTIP}</p>
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
          <span className="swatch" style={{ backgroundColor: WIND.arrowColor }} />
          Wind direction (arrow points downwind)
        </div>
      </section>

      <section>
        <div className="legend-row">
          <span className="swatch" style={{ backgroundColor: BASEMAP.stateBorder }} />
          State / UT boundary
        </div>
        <div className="legend-row">
          <span className="swatch" style={{ backgroundColor: BASEMAP.intlBorder }} />
          International boundary
        </div>
      </section>

      <section>
        <p className="muted legend-note">
          Zoomed out: a generalized nationwide picture. Zoom in for finer detail, or click any hex
          for its full readings.
        </p>
      </section>
    </div>
  )
}
