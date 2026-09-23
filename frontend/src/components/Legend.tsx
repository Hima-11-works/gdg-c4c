import { NO_DATA_COLOR, PDI_COLOR_SCALE, PM25_COLOR_SCALE } from '../lib/colorScales'
import { BASEMAP, WIND } from '../lib/mapTheme'
import { DISTRICT_BOUNDARY_COLOR, HIGHWAY_CORE_COLOR, MAJOR_ROAD_COLOR } from '../lib/visualConfig'
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
          {/* Only listed once the map is deep enough to draw them, so the key
              never claims a line that isn't there. */}
          {state.lod.tier !== 'country' && (
            <div className="legend-row" title="geoBoundaries ADM2 (ODbL) — from level 2">
              <span className="swatch" style={{ backgroundColor: DISTRICT_BOUNDARY_COLOR }} />
              District boundary
            </div>
          )}
          {state.lod.resolution >= 5 && (
            <div
              className="legend-row"
              title="Natural Earth 10m roads, major-highway class (public domain) — from level 3. Main corridors only, not the full National Highway network."
            >
              <span className="swatch" style={{ backgroundColor: HIGHWAY_CORE_COLOR }} />
              Major highway
            </div>
          )}
          {state.lod.resolution >= 6 && (
            <div
              className="legend-row"
              title="Natural Earth 10m roads, 'Road' class (public domain) — from level 4. The same source's next class down, not a full secondary-road network."
            >
              <span className="swatch" style={{ backgroundColor: MAJOR_ROAD_COLOR }} />
              Major road
            </div>
          )}
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
