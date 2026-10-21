import { useEffect, useState } from 'react'
import type { AsyncResource } from '../hooks/useApiResource'
import type { MetaV2Out } from '../lib/types'

interface DataModeBannerProps {
  label: string
  pendingLabel?: string
  pendingError?: string
  resource: AsyncResource<unknown[]>
  meta: AsyncResource<MetaV2Out>
  onRetry: () => void
  warming?: boolean
  interpolated?: boolean
}

/** A single, compact mode badge with full provenance and fetch status on hover. */
export function DataModeBanner({
  label,
  pendingLabel,
  pendingError,
  resource,
  meta,
  onRetry,
  warming = false,
  interpolated = false,
}: DataModeBannerProps) {
  const [now, setNow] = useState(() => Date.now())
  const metadata = meta.status === 'success' ? meta.data : null
  const metadataAgeSeconds =
    metadata?.age_seconds === null || metadata?.age_seconds === undefined
      ? null
      : Math.max(
          0,
          metadata.age_seconds +
            (now - (meta.status === 'success' ? (meta.lastFetchedAt ?? now) : now)) / 1000,
        )
  // Keep the client-side clock aligned with prediction_queries.STALE_RUN_SECONDS.
  const metadataIsStale =
    metadata?.is_stale === true ||
    (metadataAgeSeconds !== null && metadataAgeSeconds > 24 * 60 * 60)
  const resourceLastFetchedAt = resource.status === 'success' ? resource.lastFetchedAt : undefined
  const metadataLastFetchedAt = meta.status === 'success' ? meta.lastFetchedAt : undefined
  const lastFetchedAt = Math.max(resourceLastFetchedAt ?? 0, metadataLastFetchedAt ?? 0)

  useEffect(() => {
    if (lastFetchedAt === 0) return
    const interval = setInterval(() => setNow(Date.now()), 60_000)
    return () => clearInterval(interval)
  }, [lastFetchedAt])

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
  if (pendingLabel) {
    details.push(
      pendingError
        ? `Could not load ${pendingLabel}; showing ${label}`
        : `Loading ${pendingLabel}; showing ${label}`,
    )
  }
  if (metadata?.is_fallback) {
    details.push(
      `Illustrative fallback data, not a published run${metadata.fallback_reason ? `: ${metadata.fallback_reason}` : ''}`,
    )
  } else if (metadataIsStale) {
    details.push(`Last published run is ${formatAge(metadataAgeSeconds)} old`)
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
  if (resource.status === 'success' && resource.refreshError) {
    details.push(
      `Refresh failed for ${label}; last successful fetch was ${formatAge((now - (resource.lastFetchedAt ?? now)) / 1000)} ago`,
    )
  }
  if (meta.status === 'success' && meta.refreshError) {
    details.push(
      `Metadata refresh failed; last successful check was ${formatAge((now - (meta.lastFetchedAt ?? now)) / 1000)} ago`,
    )
  } else if (meta.status === 'error') {
    details.push(`Could not refresh publication metadata: ${meta.message}`)
  }
  if (interpolated) details.push('Interpolated between published anchors; no calibrated interval')
  if (resource.status === 'success' && resource.runId) details.push(`Run ${resource.runId}`)
  if (metadata && !metadata.is_fallback && !metadataIsStale) {
    details.push(`Published at ${new Date(metadata.generated_at).toLocaleString()}`)
  }

  const description = details.length
    ? details.join(' · ')
    : `${modeLabel.toLowerCase()} air-quality data`
  const isError = resource.status === 'error'
  const hasRefreshWarning =
    !isError &&
    (metadataIsStale ||
      (resource.status === 'success' && Boolean(resource.refreshError)) ||
      (meta.status === 'success' && Boolean(meta.refreshError)) ||
      meta.status === 'error')
  const refreshWarningLabel =
    resource.status === 'success' && resource.refreshError && !pendingLabel
      ? 'Refresh failed; showing the last successful data'
      : meta.status === 'success' && meta.refreshError
        ? `Metadata refresh failed; published run is ${formatAge(metadataAgeSeconds)} old`
        : meta.status === 'error'
          ? 'Publication metadata could not be refreshed'
          : metadataIsStale
            ? `Published run is ${formatAge(metadataAgeSeconds)} old`
            : null
  const canRetry = isError || hasRefreshWarning

  return (
    <div
      className={`banner banner-mode banner-mode-${mode ?? 'unknown'} ${isError ? 'banner-mode-error' : ''} ${hasRefreshWarning ? 'banner-mode-warning' : ''}`}
      role={isError ? 'alert' : 'status'}
      aria-live={isError ? 'assertive' : 'polite'}
      aria-label={`${modeLabel}. ${description}`}
      title={description}
    >
      {warming && <span className="spinner" aria-hidden="true" />}
      <span>{modeLabel}</span>
      {refreshWarningLabel && (
        <span className="banner-mode-refresh">{refreshWarningLabel}</span>
      )}
      {pendingLabel && (
        <span className="banner-mode-refresh">
          {pendingError
            ? `Showing ${label}; ${pendingLabel} unavailable`
            : `Showing ${label}; loading ${pendingLabel}`}
        </span>
      )}
      {canRetry && (
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
  const ageSeconds = Math.max(0, seconds)
  const hours = ageSeconds / 3600
  if (hours < 1) return `${Math.max(1, Math.round(ageSeconds / 60))} min`
  if (hours < 48) return `${Math.round(hours)} h`
  return `${Math.round(hours / 24)} days`
}
