import type { AsyncResource } from '../hooks/useApiResource'

interface StatusBannerProps {
  label: string
  resource: AsyncResource<unknown[]>
  onRetry: () => void
}

/** Surfaces the loading / error / empty-data / demo-data state of the
 * currently active base layer (current conditions or a forecast horizon).
 * Other resources (weather, alerts, cell detail) show their own inline
 * status next to where they're displayed instead of fighting for this
 * one banner. */
export function StatusBanner({ label, resource, onRetry }: StatusBannerProps) {
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

  if (resource.isDemo) {
    return (
      <div className="banner banner-demo" role="status">
        Demo data — illustrative, not measured.
      </div>
    )
  }

  return null
}
