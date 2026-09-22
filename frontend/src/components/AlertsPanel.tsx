import { useState } from 'react'
import { fetchAlerts } from '../lib/api'
import { formatNumber } from '../lib/format'
import { useApiResource } from '../hooks/useApiResource'
import { useMapUi } from '../state/MapUiContext'
import type { AlertOut, AlertSeverity } from '../lib/types'

const POLL_INTERVAL_MS = 60_000

const SEVERITY_LABEL: Record<AlertSeverity, string> = {
  watch: 'Watch',
  warning: 'Warning',
  critical: 'Critical',
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
  onSelect,
  done,
  onToggle,
}: {
  alert: AlertOut
  onSelect: () => void
  done: (action: string) => boolean
  onToggle: (action: string) => void
}) {
  return (
    <div className={`alert-item severity-${alert.severity}`}>
      <button type="button" className="alert-item-body" onClick={onSelect}>
        <strong>{SEVERITY_LABEL[alert.severity]}</strong>
        <span>{alert.message}</span>
        <span className="muted">
          Now: {formatNumber(alert.current_pm25)} µg/m³
          {alert.forecast_pm25 !== null && (
            <>
              {' '}
              · +{alert.forecast_hours}h: {formatNumber(alert.forecast_pm25)} µg/m³
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
  // The checklist is per-alert and lives here (not inside AlertItem) so a
  // 60s poll re-rendering the list cannot wipe what an operator ticked.
  const [checked, setChecked] = useState<Set<string>>(new Set())
  const { dispatch } = useMapUi()
  const { resource, refetch } = useApiResource(
    () => fetchAlerts(publishedRunId),
    [publishedRunId],
    {
      pollIntervalMs: POLL_INTERVAL_MS,
      enabled: publishedRunId !== undefined,
    },
  )

  const count = resource.status === 'success' ? resource.data.length : 0

  const keyFor = (alert: AlertOut, action: string) =>
    `${alert.h3_cell}-${alert.created_at}-${action}`
  const toggle = (alert: AlertOut, action: string) =>
    setChecked((prev) => {
      const next = new Set(prev)
      const key = keyFor(alert, action)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })

  return (
    <div className="panel alerts-panel">
      <button
        type="button"
        className="alerts-toggle"
        onClick={() => setOpen((value) => !value)}
        aria-label={count > 0 ? `Alerts, ${count} active` : 'Alerts, none active'}
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
        {count > 0 && <span className="alert-badge">{count > 99 ? '99+' : count}</span>}
      </button>

      {open && (
        <div className="alerts-list">
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

          {resource.status === 'success' && resource.data.length === 0 && <p>No active alerts.</p>}

          {resource.status === 'success' && resource.data.length > 0 && (
            <p className="muted alerts-checklist-note">
              The checklist below is local to this session — this dashboard cannot dispatch or
              notify authorities yet.
            </p>
          )}

          {resource.status === 'success' &&
            resource.data.map((alert) => (
              <AlertItem
                key={`${alert.h3_cell}-${alert.created_at}`}
                alert={alert}
                onSelect={() => dispatch({ type: 'SELECT_CELL', cell: alert.h3_cell })}
                done={(action) => checked.has(keyFor(alert, action))}
                onToggle={(action) => toggle(alert, action)}
              />
            ))}
        </div>
      )}
    </div>
  )
}
