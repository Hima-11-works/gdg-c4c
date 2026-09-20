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

/** Authority assigned per alert, by severity — escalation ladder as the
 *  action-oriented alert centre routes cases up the intervention chain. */
const ASSIGNED_AUTHORITY: Record<AlertSeverity, string> = {
  watch: 'Municipal Air Quality Monitoring Cell',
  warning: 'State Pollution Control Board Rapid Action Unit',
  critical: 'CPCB Emergency Response Task Force',
}

/** Authority intervention actions surfaced per alert severity. */
const AUTHORITY_ACTIONS: Record<AlertSeverity, string[]> = {
  watch: ['Issue Advisory'],
  warning: ['Dispatch Anti-Smog Gun', 'Issue Enforcement Notice'],
  critical: ['Dispatch Anti-Smog Gun', 'Issue Enforcement Notice', 'Escalate to Cabinet'],
}

function AlertItem({ alert, onSelect }: { alert: AlertOut; onSelect: () => void }) {
  const [acknowledged, setAcknowledged] = useState<Set<string>>(new Set())

  const acknowledge = (action: string) =>
    setAcknowledged((prev) => {
      const next = new Set(prev)
      next.add(action)
      return next
    })

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
        <span className="alert-assigned">Assigned: {ASSIGNED_AUTHORITY[alert.severity]}</span>
        <div className="alert-actions">
          {AUTHORITY_ACTIONS[alert.severity].map((action) => {
            const done = acknowledged.has(action)
            return (
              <button
                key={action}
                type="button"
                className={`alert-cta ${done ? 'alert-cta-done' : ''}`}
                onClick={() => acknowledge(action)}
                aria-pressed={done}
                title={done ? 'Dispatch acknowledged on the federated edge' : `Trigger ${action}`}
              >
                {done ? '✓ ' : ''}
                {action}
              </button>
            )
          })}
        </div>
      </div>
    </div>
  )
}

export function AlertsPanel() {
  const [open, setOpen] = useState(false)
  const { dispatch } = useMapUi()
  const { resource, refetch } = useApiResource(fetchAlerts, [], {
    pollIntervalMs: POLL_INTERVAL_MS,
  })

  const count = resource.status === 'success' ? resource.data.length : 0

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

          {resource.status === 'success' &&
            resource.data.map((alert) => (
              <AlertItem
                key={`${alert.h3_cell}-${alert.created_at}`}
                alert={alert}
                onSelect={() => dispatch({ type: 'SELECT_CELL', cell: alert.h3_cell })}
              />
            ))}
        </div>
      )}
    </div>
  )
}
