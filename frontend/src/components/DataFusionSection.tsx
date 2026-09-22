// "Data fusion" section of the inspection drawer.
//
// It sits in one panel because the point of the panel is that these are
// DIFFERENT sources about the same cell, and the honest version of that is to
// show each one as itself - not to blend them into a single invented score:
//
//   - Thermal: real NASA FIRMS detections (VIIRS), shown as the worst FRP in
//     this cell plus how many detections there are.
//   - Citizen reports: real submissions stored by the backend, counted.
//   - Satellite NO2: a Sentinel-5P TROPOMI *raster overlay*. It is a daily
//     column image, not a per-cell number, so there is no value to print here
//     and none is invented - the row says whether the layer is on and where
//     the real data is coming from.
//
// Everything is either a stored value or explicitly "no data"; nothing is
// estimated, weighted, or expressed as a sigma.

import { activeFiresInCell } from '../lib/activeFires'
import { minutesAgo, reportsInCell } from '../lib/citizenReports'
import { NO2_USES_WMS, NO2_WMS_URL } from '../lib/satelliteImagery'
import { useMapUi } from '../state/MapUiContext'
import type { AsyncResource } from '../hooks/useApiResource'
import type { ActiveFire } from '../lib/activeFires'
import type { FireReportOut } from '../lib/types'

function resourceValue<T>(resource: AsyncResource<T[]>, render: (data: T[]) => string): string {
  if (resource.status === 'loading') return 'Loading…'
  if (resource.status === 'error') return 'Unavailable'
  // 'idle' means the fetch hasn't been asked for (the layer is off), so there
  // is genuinely nothing to report - not an empty result.
  if (resource.status === 'idle') return 'Layer off'
  return render(resource.data)
}

export function DataFusionSection({
  h3Cell,
  activeFires,
  citizenReports,
}: {
  h3Cell: string
  activeFires: AsyncResource<ActiveFire[]>
  citizenReports: AsyncResource<FireReportOut[]>
}) {
  const { state } = useMapUi()

  const thermal = resourceValue(activeFires, (fires) => {
    const inCell = activeFiresInCell(fires, h3Cell)
    if (inCell.length === 0) return 'No detection in this cell (last 24 h)'
    const worst = inCell[0]
    const count = inCell.length === 1 ? '1 detection' : `${inCell.length} detections`
    const frp = worst.frp === null ? 'FRP not reported' : `${worst.frp.toFixed(1)} MW FRP`
    return `${frp} — worst of ${count}`
  })

  const reports = resourceValue(citizenReports, (all) => {
    const inCell = reportsInCell(all, h3Cell)
    if (inCell.length === 0) return 'No reports in this cell'
    const newest = inCell[0]
    const count = inCell.length === 1 ? '1 report' : `${inCell.length} reports`
    return `${count} — newest ${minutesAgo(newest.reported_at)} mins ago`
  })

  const no2 = state.showIndustrialEmissions
    ? NO2_USES_WMS
      ? 'Overlay on — WMS endpoint (visual raster, no per-cell value)'
      : 'Overlay on — daily TROPOMI column via NASA GIBS (visual raster, no per-cell value)'
    : 'Overlay off — enable "Satellite NO2 Emissions" to view it'

  return (
    <section className="data-fusion">
      <h3>Data fusion</h3>
      <p className="muted data-fusion-note">
        Three independent sources for this cell. They are shown side by side, not combined into a
        single score — the platform has no fused index.
      </p>
      <dl className="data-fusion-list">
        <dt>Thermal (VIIRS)</dt>
        <dd>{thermal}</dd>

        <dt>Citizen reports</dt>
        <dd>{reports}</dd>

        <dt>Satellite NO2 (Sentinel-5P)</dt>
        <dd>{no2}</dd>
      </dl>
      <p className="muted data-fusion-source">
        Sources: NASA FIRMS (VIIRS thermal detections) · citizen submissions via POST
        /api/v1/reports · ESA Copernicus Sentinel-5P TROPOMI
        {NO2_USES_WMS && NO2_WMS_URL !== undefined ? ` (WMS: ${NO2_WMS_URL})` : ' via NASA GIBS'}.
      </p>
    </section>
  )
}
