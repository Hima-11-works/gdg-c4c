import type { AsyncResource } from '../hooks/useApiResource'

interface StatusBannerProps {
  label: string
  resource: AsyncResource<unknown[]>
  onRetry: () => void
  /** True while the current map view's forecast frames are being fetched
   *  into the cache (see lib/forecastFrames.ts's warmForecastWindow). Takes
   *  priority over the other states — it's an active, short-lived operation. */
  warming?: boolean
  /** The selected 15-minute frame was interpolated between published anchors. */
  interpolated?: boolean
}

/** Surfaces the loading / error / empty-data / demo-data state of the
 * currently active base layer (current conditions or a forecast horizon).
 * Other resources (weather, alerts, cell detail) show their own inline
 * status next to where they're displayed instead of fighting for this
 * one banner. */
export function StatusBanner({
  label,
  resource,
  onRetry,
  warming = false,
  interpolated = false,
}: StatusBannerProps) {
  if (warming) {
    return (
      <div className="banner banner-info" role="status" aria-live="polite">
        <span className="spinner" aria-hidden="true" />
        Fetching and caching local results
      </div>
    )
  }

  if (resource.status === 'idle' || resource.status === 'loading') {
    return (
      <div className="banner banner-info" role="status">
        <span className="spinner" aria-hidden="true" />
        Loading {label}…
      </div>
    )
  }

  if (resource.status === 'error') {
    return (
      <div className="banner banner-error" role="alert">
        Can't reach the backend for {label}: {resource.message}
        <button type="button" onClick={onRetry}>
          Retry
        </button>
      </div>
    )
  }

  if (resource.data.length === 0) {
    return (
      <div className="banner banner-info" role="status">
        No H3 cells reported for {label} yet.
      </div>
    )
  }

  if (resource.mode === 'demo' || (resource.mode === undefined && resource.isDemo)) {
    return (
      <div className="banner banner-demo" role="status">
        Demo simulation · illustrative, not measured
        {interpolated ? ' · interpolated between published anchors; no calibrated interval' : ''}
        {resource.runId ? ` · ${resource.runId}` : ''}
      </div>
    )
  }

  if (resource.mode === 'mixed') {
    return (
      <div className="banner banner-info" role="status">
        Mixed observed and modeled inputs
        {interpolated ? ' · interpolated between published anchors; no calibrated interval' : ''}
        {resource.runId ? ` · run ${resource.runId}` : ''}
      </div>
    )
  }

  if (resource.mode === 'live') {
    const issued = resource.generatedAt
      ? new Date(resource.generatedAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
      : null
    return (
      <div className="banner banner-info" role="status">
        Published live run{issued ? ` · issued ${issued}` : ''}
        {interpolated ? ' · interpolated between published anchors; no calibrated interval' : ''}
        {resource.runId ? ` · ${resource.runId}` : ''}
      </div>
    )
  }

  return null
}
