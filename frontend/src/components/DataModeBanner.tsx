import type { AsyncResource } from '../hooks/useApiResource'
import type { MetaV2Out } from '../lib/types'

interface DataModeBannerProps {
  label: string
  resource: AsyncResource<unknown[]>
  meta: AsyncResource<MetaV2Out>
  onRetry: () => void
  warming?: boolean
  interpolated?: boolean
}

/** A single, compact mode badge with full provenance and fetch status on hover. */
export function DataModeBanner({
  label,
  resource,
  meta,
  onRetry,
  warming = false,
  interpolated = false,
}: DataModeBannerProps) {
  const metadata = meta.status === 'success' ? meta.data : null
  const mode = metadata
    ? metadata.is_fallback
      ? 'demo'
      : metadata.data_mode
    : resource.status === 'success'
      ? (resource.mode ?? (resource.isDemo ? 'demo' : 'live'))
      : null
  const modeLabel =
    mode === 'demo'
      ? 'DEMO SIM'
      : mode === 'mixed'
        ? 'MIXED DATA'
        : mode === 'live'
          ? 'LIVE DATA'
          : 'CHECKING DATA'

  const details: string[] = []
  if (metadata?.is_fallback) {
    details.push(
      `Illustrative fallback data, not a published run${metadata.fallback_reason ? `: ${metadata.fallback_reason}` : ''}`,
    )
  } else if (metadata?.is_stale) {
    details.push(`Last published run is ${formatAge(metadata.age_seconds)} old`)
  } else if (mode === 'demo') {
    details.push('Illustrative data, not measured')
  } else if (mode === 'mixed') {
    details.push('Combines observed and modeled inputs')
  }

  if (warming) details.push('Fetching and caching local results')
  else if (resource.status === 'idle' || resource.status === 'loading')
    details.push(`Loading ${label}`)
  else if (resource.status === 'error')
    details.push(`Can't reach the backend for ${label}: ${resource.message}`)
  else if (resource.data.length === 0) details.push(`No H3 cells reported for ${label} yet`)
  if (interpolated) details.push('Interpolated between published anchors; no calibrated interval')
  if (resource.status === 'success' && resource.runId) details.push(`Run ${resource.runId}`)
  if (metadata && !metadata.is_fallback && !metadata.is_stale) {
    details.push(`Published at ${new Date(metadata.generated_at).toLocaleString()}`)
  }

  const description = details.length
    ? details.join(' · ')
    : `${modeLabel.toLowerCase()} air-quality data`
  const isError = resource.status === 'error'

  return (
    <div
      className={`banner banner-mode banner-mode-${mode ?? 'unknown'} ${isError ? 'banner-mode-error' : ''}`}
      role={isError ? 'alert' : 'status'}
      aria-live={isError ? 'assertive' : 'polite'}
      aria-label={`${modeLabel}. ${description}`}
      title={description}
    >
      {warming && <span className="spinner" aria-hidden="true" />}
      <span>{modeLabel}</span>
      {isError && (
        <button
          type="button"
          className="banner-mode-retry"
          onClick={onRetry}
          aria-label={`Retry ${label}`}
        >
          Retry
        </button>
      )}
    </div>
  )
}

function formatAge(seconds: number | null): string {
  if (seconds === null) return 'an unknown amount of time'
  const hours = seconds / 3600
  if (hours < 1) return `${Math.max(1, Math.round(seconds / 60))} min`
  if (hours < 48) return `${Math.round(hours)} h`
  return `${Math.round(hours / 24)} days`
}
