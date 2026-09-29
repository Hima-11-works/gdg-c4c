import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { fetchAlerts } from '../lib/api'
import { formatNumber } from '../lib/format'
import { cellCenter, resolutionOfCell } from '../lib/h3Geometry'
import { loadLocations, type IndiaLocation } from '../lib/locations'
import { useApiResource } from '../hooks/useApiResource'
import { useMapUi } from '../state/MapUiContext'
import type { AlertOut, AlertSeverity } from '../lib/types'

const ALERT_PAGE_SIZE = 100
const EMPTY_ALERTS: AlertOut[] = []

const SEVERITY_LABEL: Record<AlertSeverity, string> = {
  watch: 'Watch',
  warning: 'Warning',
  critical: 'Critical',
}

function formatForecastTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? 'unreadable time'
    : `${date.toLocaleString('en-GB', { timeZone: 'UTC' })} UTC`
}

function nearestPlaceLabel(
  latitude: number,
  longitude: number,
  locations: IndiaLocation[],
): string | null {
  let nearest: IndiaLocation | undefined
  let nearestDistance = Number.POSITIVE_INFINITY
  const longitudeScale = Math.cos((latitude * Math.PI) / 180)

  for (const location of locations) {
    const latDelta = location.lat - latitude
    const lonDelta = (location.lon - longitude) * longitudeScale
    const distance = latDelta * latDelta + lonDelta * lonDelta
    if (distance < nearestDistance) {
      nearest = location
      nearestDistance = distance
    }
  }

  if (!nearest) return null
  return nearest.n.toLowerCase() === nearest.s.toLowerCase()
    ? nearest.n
    : `${nearest.n}, ${nearest.s}`
}

async function copyCellId(cellId: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(cellId)
      return true
    }
  } catch {
    // Fall through to the legacy copy path for browsers that deny clipboard
    // access despite the click being a user gesture.
  }

  const input = document.createElement('textarea')
  input.value = cellId
  input.setAttribute('readonly', '')
  input.style.position = 'fixed'
  input.style.opacity = '0'
  document.body.appendChild(input)
  try {
    input.select()
    return document.execCommand('copy')
  } catch {
    return false
  } finally {
    input.remove()
  }
}

/** Suggested escalation path per severity - a prompt for whoever is reading
 *  the alert, NOT an assignment: this dashboard has no authority-routing
 *  backend, so nothing is dispatched, notified or recorded. */
const SUGGESTED_AUTHORITY: Record<AlertSeverity, string> = {
  watch: 'Municipal Air Quality Monitoring Cell',
  warning: 'State Pollution Control Board Rapid Action Unit',
  critical: 'CPCB Emergency Response Task Force',
}

/** Suggested response actions per severity - a local checklist of what an
 *  operator might do by hand. Ticking one does not send anything. */
const SUGGESTED_ACTIONS: Record<AlertSeverity, string[]> = {
  watch: ['Issue advisory'],
  warning: ['Inspect site', 'Issue enforcement notice'],
  critical: ['Inspect site', 'Issue enforcement notice', 'Escalate to CPCB'],
}

function AlertItem({
  alert,
  locationLabel,
  onSelect,
  selecting,
  done,
  onToggle,
}: {
  alert: AlertOut
  locationLabel: string | null
  onSelect: () => void
  selecting: boolean
  done: (action: string) => boolean
  onToggle: (action: string) => void
}) {
  return (
    <div className={`alert-item severity-${alert.severity}`}>
      <button
        type="button"
        className="alert-item-body"
        onClick={onSelect}
        disabled={selecting}
        aria-label={`Copy cell ID ${alert.h3_cell} and focus its location on the map`}
      >
        <strong>{SEVERITY_LABEL[alert.severity]}</strong>
        <span>{alert.message}</span>
        <span className="muted alert-cell-location">
          {locationLabel ? `Nearest mapped place: ${locationLabel} · ` : ''}Cell ID:{' '}
          <code>{alert.h3_cell}</code>
        </span>
        <span className="muted">
          Now: {formatNumber(alert.current_pm25)} µg/m³
          {alert.forecast_pm25 !== null && (
            <>
              {' '}
              · +{alert.forecast_hours ?? '?'}h: {formatNumber(alert.forecast_pm25)} µg/m³
              {alert.forecast_time && <> · valid {formatForecastTime(alert.forecast_time)}</>}
            </>
          )}
          {alert.confidence !== null && <> · {Math.round(alert.confidence * 100)}% confidence</>}
        </span>
      </button>

      <div className="alert-authority">
        <span className="alert-assigned">
          Suggested escalation: {SUGGESTED_AUTHORITY[alert.severity]}
        </span>
        <div className="alert-actions">
          {SUGGESTED_ACTIONS[alert.severity].map((action) => {
            const checked = done(action)
            return (
              <button
                key={action}
                type="button"
                className={`alert-cta ${checked ? 'alert-cta-done' : ''}`}
                onClick={() => onToggle(action)}
                aria-pressed={checked}
                title={
                  checked
                    ? `${action} - ticked locally (nothing is sent)`
                    : `Tick ${action} off on this local checklist`
                }
              >
                {checked ? '✓ ' : ''}
                {action}
              </button>
            )
          })}
        </div>
      </div>
    </div>
  )
}

export function AlertsPanel({ publishedRunId }: { publishedRunId?: string }) {
  const [open, setOpen] = useState(false)
  // The checklist is per-alert and lives here (not inside AlertItem) so
  // loading later pages cannot wipe what an operator ticked.
  const [checked, setChecked] = useState<Set<string>>(new Set())
  const [pageState, setPageState] = useState<{ runId: string | undefined; pages: AlertOut[][] }>({
    runId: publishedRunId,
    pages: [],
  })
  const [loadingMoreFor, setLoadingMoreFor] = useState<string | undefined>()
  const [loadMoreError, setLoadMoreError] = useState<string | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [locations, setLocations] = useState<IndiaLocation[] | null>(null)
  const [locationsError, setLocationsError] = useState(false)
  const [selectingCell, setSelectingCell] = useState<string | null>(null)
  const [copyError, setCopyError] = useState<string | null>(null)
  const { dispatch } = useMapUi()
  const dialogRef = useRef<HTMLElement>(null)
  const { resource, refetch } = useApiResource(
    () => fetchAlerts(publishedRunId, ALERT_PAGE_SIZE, 0),
    [publishedRunId],
    {
      enabled: publishedRunId !== undefined,
    },
  )

  const extraPages = useMemo(
    () => (pageState.runId === publishedRunId ? pageState.pages : []),
    [pageState.pages, pageState.runId, publishedRunId],
  )
  const baseAlerts = resource.status === 'success' ? resource.data : EMPTY_ALERTS
  const visibleAlerts = useMemo(
    () => [...baseAlerts, ...extraPages.flat()],
    [baseAlerts, extraPages],
  )
  const locationLabels = useMemo(() => {
    const labels = new Map<string, string>()
    if (locations === null) return labels
    for (const cellId of new Set(visibleAlerts.map((alert) => alert.h3_cell))) {
      try {
        const [latitude, longitude] = cellCenter(cellId)
        const label = nearestPlaceLabel(latitude, longitude, locations)
        if (label) labels.set(cellId, label)
      } catch {
        // Keep malformed cell IDs searchable and focusable by their raw value.
      }
    }
    return labels
  }, [locations, visibleAlerts])
  const normalizedQuery = searchQuery.trim().toLocaleLowerCase()
  const loadingLocationSearch = normalizedQuery.length > 0 && locations === null && !locationsError
  const filteredAlerts = useMemo(() => {
    if (!normalizedQuery) return visibleAlerts
    const queryResolution = resolutionOfCell(normalizedQuery)
    const isFullCellId = queryResolution !== undefined && queryResolution >= 0
    return visibleAlerts.filter((alert) => {
      const cellId = alert.h3_cell.toLocaleLowerCase()
      if (isFullCellId) return cellId === normalizedQuery
      return [alert.message, locationLabels.get(alert.h3_cell) ?? ''].some((value) =>
        value.toLocaleLowerCase().includes(normalizedQuery),
      )
    })
  }, [locationLabels, normalizedQuery, visibleAlerts])
  const count = visibleAlerts.length
  const filteredCount = filteredAlerts.length
  const lastPage = extraPages.at(-1) ?? (resource.status === 'success' ? resource.data : [])
  const hasMore = resource.status === 'success' && lastPage.length === ALERT_PAGE_SIZE
  const loadingMore = publishedRunId !== undefined && loadingMoreFor === publishedRunId

  useEffect(() => {
    if (!open || locations !== null) return
    let cancelled = false
    loadLocations()
      .then((items) => {
        if (!cancelled) setLocations(items)
      })
      .catch(() => {
        if (!cancelled) setLocationsError(true)
      })
    return () => {
      cancelled = true
    }
  }, [locations, open])

  const loadMore = async () => {
    if (publishedRunId === undefined || loadingMoreFor !== undefined || !hasMore) return
    const requestRunId = publishedRunId
    const offset = visibleAlerts.length
    setLoadingMoreFor(requestRunId)
    setLoadMoreError(null)
    try {
      const nextPage = await fetchAlerts(requestRunId, ALERT_PAGE_SIZE, offset)
      setPageState((current) => ({
        runId: requestRunId,
        pages: [...(current.runId === requestRunId ? current.pages : []), nextPage.data],
      }))
    } catch (error) {
      setLoadMoreError(error instanceof Error ? error.message : 'Could not load more alerts.')
    } finally {
      setLoadingMoreFor((current) => (current === requestRunId ? undefined : current))
    }
  }

  const keyFor = (alert: AlertOut, action: string) =>
    `${alert.h3_cell}-${alert.created_at}-${alert.forecast_hours}-${action}`
  const toggle = (alert: AlertOut, action: string) =>
    setChecked((prev) => {
      const next = new Set(prev)
      const key = keyFor(alert, action)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })

  const selectAlert = async (alert: AlertOut) => {
    if (selectingCell !== null) return
    setSelectingCell(alert.h3_cell)
    setCopyError(null)
    try {
      const copied = await copyCellId(alert.h3_cell)
      if (!copied) {
        setCopyError(
          `Could not copy cell ID ${alert.h3_cell}. Check clipboard access and try again.`,
        )
        return
      }

      setOpen(false)
      if (alert.forecast_hours !== null) {
        dispatch({
          type: 'SELECT_FORECAST',
          minutes: Math.round(alert.forecast_hours * 60),
        })
      }
      dispatch({ type: 'FOCUS_CELL', cell: alert.h3_cell })
    } finally {
      setSelectingCell(null)
    }
  }

  useEffect(() => {
    if (!open) return
    const dialog = dialogRef.current
    if (dialog === null) return
    const previousFocus =
      document.activeElement instanceof HTMLElement ? document.activeElement : null
    const focusableSelector =
      'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
    const focusableItems = () =>
      Array.from(dialog.querySelectorAll<HTMLElement>(focusableSelector)).filter(
        (item) => item.getClientRects().length > 0 && item.getAttribute('aria-hidden') !== 'true',
      )

    const first = focusableItems()[0]
    if (first) first.focus()
    else dialog.focus()

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpen(false)
        return
      }
      if (event.key !== 'Tab') return

      const items = focusableItems()
      const firstItem = items[0]
      const lastItem = items.at(-1)
      if (firstItem === undefined || lastItem === undefined) {
        event.preventDefault()
        dialog.focus()
      } else if (
        event.shiftKey &&
        (document.activeElement === firstItem || !dialog.contains(document.activeElement))
      ) {
        event.preventDefault()
        lastItem.focus()
      } else if (
        !event.shiftKey &&
        (document.activeElement === lastItem || !dialog.contains(document.activeElement))
      ) {
        event.preventDefault()
        firstItem.focus()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => {
      window.removeEventListener('keydown', onKeyDown)
      if (previousFocus?.isConnected) previousFocus.focus()
    }
  }, [open])

  return (
    <div className="panel alerts-panel">
      <button
        type="button"
        className="alerts-toggle"
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="dialog"
        aria-controls="alerts-dialog"
        aria-label={
          resource.status !== 'success'
            ? 'Alerts'
            : count >= ALERT_PAGE_SIZE
              ? 'Alerts, 100 or more active'
              : count > 0
                ? `Alerts, ${count} active`
                : 'Alerts, none active'
        }
        aria-expanded={open}
      >
        <svg className="bell-icon" viewBox="0 0 24 24" aria-hidden="true">
          <path
            d="M12 2a6 6 0 0 0-6 6v3.5L4.3 15a1 1 0 0 0 .9 1.5h13.6a1 1 0 0 0 .9-1.5L18 11.5V8a6 6 0 0 0-6-6Z"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinejoin="round"
          />
          <path
            d="M9.5 19a2.5 2.5 0 0 0 5 0"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
          />
        </svg>
        {resource.status === 'success' && count > 0 && (
          <span className="alert-badge">{count >= ALERT_PAGE_SIZE ? '99+' : count}</span>
        )}
      </button>

      {open &&
        createPortal(
          <div
            className="photo-review-backdrop alerts-backdrop"
            role="presentation"
            onMouseDown={(event) => {
              if (event.target === event.currentTarget) setOpen(false)
            }}
          >
            <section
              className="panel alerts-list"
              id="alerts-dialog"
              role="dialog"
              aria-modal="true"
              aria-labelledby="alerts-title"
              tabIndex={-1}
              ref={dialogRef}
            >
              <header className="alerts-dialog-header">
                <h2 id="alerts-title">Alerts</h2>
                <button
                  type="button"
                  className="report-form-close"
                  onClick={() => setOpen(false)}
                  aria-label="Close alerts"
                  title="Close"
                >
                  ×
                </button>
              </header>
              <div className="alert-search">
                <input
                  type="search"
                  value={searchQuery}
                  onChange={(event) => setSearchQuery(event.target.value)}
                  placeholder="Search by location or full cell ID"
                  aria-label="Search alerts by location or full cell ID"
                />
                {searchQuery && (
                  <button
                    type="button"
                    className="alert-search-clear"
                    onClick={() => setSearchQuery('')}
                    aria-label="Clear alert search"
                  >
                    Clear
                  </button>
                )}
              </div>
              {loadingLocationSearch && (
                <p className="muted alerts-checklist-note" role="status">
                  Loading place names… cell ID and alert text search are available now.
                </p>
              )}
              {locationsError && normalizedQuery && (
                <p className="muted alerts-checklist-note" role="status">
                  Place names could not be loaded. Search still checks cell IDs and alert text.
                </p>
              )}
              {copyError && (
                <p className="alerts-load-error" role="alert">
                  {copyError}
                </p>
              )}
              {resource.status === 'loading' && <p>Loading alerts…</p>}

              {resource.status === 'error' && (
                <p>
                  Couldn't load alerts: {resource.message}{' '}
                  <button type="button" onClick={refetch}>
                    Retry
                  </button>
                </p>
              )}

              {resource.status === 'success' && resource.isDemo && (
                <p className="banner banner-demo" role="status">
                  Demo data — illustrative, not measured.
                </p>
              )}

              {resource.status === 'success' && resource.data.length > 0 && (
                <p className="muted alerts-checklist-note">
                  The checklist below is local to this session — this dashboard cannot dispatch or
                  notify authorities yet.
                </p>
              )}

              {resource.status === 'success' && (
                <p className="muted alerts-checklist-note" role="status">
                  {normalizedQuery
                    ? `Showing ${filteredCount.toLocaleString()} matching alerts from ${count.toLocaleString()} loaded`
                    : `Showing ${count.toLocaleString()} alert${count === 1 ? '' : 's'}`}
                  {hasMore
                    ? normalizedQuery
                      ? ' · load more to search additional alerts'
                      : ' · 100 or more in this run'
                    : normalizedQuery
                      ? ' · all alerts in this run are loaded'
                      : ' · all in this run'}
                  .
                </p>
              )}

              {resource.status === 'success' &&
                filteredAlerts.length === 0 &&
                !loadingLocationSearch && (
                  <p className="muted" role="status">
                    {normalizedQuery ? 'No alerts match this search.' : 'No active alerts.'}
                  </p>
                )}

              {resource.status === 'success' &&
                filteredAlerts.map((alert) => (
                  <AlertItem
                    key={`${alert.h3_cell}-${alert.created_at}-${alert.forecast_hours}`}
                    alert={alert}
                    locationLabel={locationLabels.get(alert.h3_cell) ?? null}
                    selecting={selectingCell === alert.h3_cell}
                    onSelect={() => void selectAlert(alert)}
                    done={(action) => checked.has(keyFor(alert, action))}
                    onToggle={(action) => toggle(alert, action)}
                  />
                ))}

              {loadMoreError !== null && (
                <p role="alert" className="alerts-load-error">
                  Couldn't load more alerts: {loadMoreError}{' '}
                  <button type="button" onClick={() => void loadMore()}>
                    Retry
                  </button>
                </p>
              )}

              {hasMore && (
                <button
                  type="button"
                  className="alert-cta"
                  onClick={() => void loadMore()}
                  disabled={loadingMore}
                >
                  {loadingMore ? 'Loading alerts…' : `Load next ${ALERT_PAGE_SIZE} alerts`}
                </button>
              )}
            </section>
          </div>,
          document.body,
        )}
    </div>
  )
}
