import { NO_DATA_COLOR, PDI_COLOR_SCALE, PM25_COLOR_SCALE } from '../lib/colorScales'
import { BASEMAP, WIND } from '../lib/mapTheme'
import { PDI_LABEL, PDI_TOOLTIP } from '../lib/format'
import { useMapUi } from '../state/MapUiContext'
import { SidePanel } from './SidePanel'

export function Legend() {
  const { state, dispatch } = useMapUi()

  return (
    <SidePanel
      id="legend-panel"
      side="left"
      open={state.legendOpen}
      onToggle={() => dispatch({ type: 'TOGGLE_LEGEND' })}
      label={state.legendOpen ? 'Hide legend' : 'Show legend'}
    >
      <div className="panel legend">
        <section>
          <h3>
            {state.mapMetric === 'populationExposure'
              ? 'Population-weighted PM2.5 (µg/m³)'
              : 'PM2.5 (µg/m³)'}
          </h3>
          {state.mapMetric === 'populationExposure' && (
            <p className="muted legend-note">
              Population-weighted concentration, not individual dose. Unavailable where population
              is unknown.
            </p>
          )}
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
            <span className="swatch" style={{ backgroundColor: WIND.pulse }} />
            Wind current (flows downwind)
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
    </SidePanel>
  )
}
