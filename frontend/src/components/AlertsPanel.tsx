import { useState } from 'react'
import { fetchAlerts } from '../lib/api'
import { formatNumber } from '../lib/format'
import {
  RESPONSE_HANDLED_BY,
  RESPONSE_LABEL,
  RESPONSE_REFERENCE_NOTE,
  RESPONSE_STEPS,
  RESPONSE_STEPS_NOTE,
} from '../lib/responseTypes'
import { useApiResource } from '../hooks/useApiResource'
import { useMapUi } from '../state/MapUiContext'
import { IncidentNotebook } from './IncidentNotebook'
import type { IncidentEvidence } from '../lib/incidentNotebook'
import type { AlertOut, AlertSeverity } from '../lib/types'

const POLL_INTERVAL_MS = 60_000

const SEVERITY_LABEL: Record<AlertSeverity, string> = {
  watch: 'Watch',
  warning: 'Warning',
  critical: 'Critical',
}

/**
 * The evidence an alert contributes to an incident: the alert record itself,
 * named by the cell and time the backend raised it at. This is a link, not a
 * copy — the notebook stores the reference, and the alert stays where it came
 * from (GET /api/v1/alerts).
 */
function alertEvidence(alert: AlertOut): IncidentEvidence[] {
  return [
    {
      source: 'alert',
      ref: alert.h3_cell,
      summary: `${SEVERITY_LABEL[alert.severity]} PM2.5 alert: ${alert.message}`,
      at: alert.created_at,
    },
  ]
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
  const [notebookOpen, setNotebookOpen] = useState(false)

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

      <div className="alert-response">
        {/* Named explicitly, because the app also carries a fire response and
            the two must never be described in each other's terms. */}
        <span className={`alert-response-kind alert-response-kind-pollution`}>
          {RESPONSE_LABEL.pollution}
        </span>
        <span className="muted alert-response-reference">
          {RESPONSE_HANDLED_BY.pollution}. {RESPONSE_REFERENCE_NOTE}
        </span>
        <div className="alert-actions">
          {RESPONSE_STEPS.pollution.map((action) => {
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
                    ? `${action} - ticked on this device only (nothing is sent)`
                    : `Tick ${action} off on the checklist kept on this device`
                }
              >
                {checked ? '✓ ' : ''}
                {action}
              </button>
            )
          })}
        </div>
        <button
          type="button"
          className="alert-incident-toggle"
          aria-expanded={notebookOpen}
          onClick={() => setNotebookOpen((value) => !value)}
        >
          {notebookOpen ? 'Hide local incident' : 'Local incident…'}
        </button>
        {notebookOpen && (
          <IncidentNotebook
            kind="pollution"
            h3Cell={alert.h3_cell}
            title={`${SEVERITY_LABEL[alert.severity]} PM2.5 alert`}
            evidence={alertEvidence(alert)}
          />
        )}
      </div>
    </div>
  )
}

export function AlertsPanel({ publishedRunId }: { publishedRunId?: string }) {
  const [open, setOpen] = useState(false)
  // The checklist is per-alert and lives here (not inside AlertItem) so a
  // 60s poll re-rendering the list cannot wipe what an operator ticked.
  // Session-only on purpose: it is a scratch list, not a record.
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

  // A published run can raise many alerts for one cell at the same instant —
  // the key has to include the severity and message, or React sees duplicates
  // (it warned about exactly that) and the checklist entry for one alert
  // silently ticks another.
  const keyFor = (alert: AlertOut, action: string) =>
    `${alert.h3_cell}-${alert.created_at}-${alert.severity}-${alert.message}-${action}`
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
              Each alert is a {RESPONSE_LABEL.pollution.toLowerCase()} — an area, not a source.
              The checklist and any incident notebook are kept on this device; nothing is
              dispatched, notified or shared. {RESPONSE_STEPS_NOTE}
            </p>
          )}

          {resource.status === 'success' &&
            resource.data.map((alert, index) => (
              <AlertItem
                // The index is part of the key because the alert API exposes no
                // id and a run genuinely returns byte-identical alerts (same
                // cell, time, severity and message), which React rejected as
                // duplicate keys. Nothing better is available to key on.
                key={`${alert.h3_cell}-${alert.created_at}-${alert.severity}-${alert.message}-${index}`}
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
