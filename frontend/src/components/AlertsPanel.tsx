import { useState } from 'react'
import { fetchAlerts } from '../lib/api'
import { useApiResource } from '../hooks/useApiResource'
import { useMapUi } from '../state/MapUiContext'
import type { AlertSeverity } from '../lib/types'

const POLL_INTERVAL_MS = 60_000

const SEVERITY_LABEL: Record<AlertSeverity, string> = {
  watch: 'Watch',
  warning: 'Warning',
  critical: 'Critical',
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
      <button type="button" className="alerts-toggle" onClick={() => setOpen((value) => !value)}>
        Alerts {count > 0 ? `(${count})` : ''}
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

          {resource.status === 'success' && resource.data.length === 0 && <p>No active alerts.</p>}

          {resource.status === 'success' &&
            resource.data.map((alert) => (
              <button
                type="button"
                key={`${alert.h3_cell}-${alert.created_at}`}
                className={`alert-item severity-${alert.severity}`}
                onClick={() => dispatch({ type: 'SELECT_CELL', cell: alert.h3_cell })}
              >
                <strong>{SEVERITY_LABEL[alert.severity]}</strong>
                <span>{alert.message}</span>
              </button>
            ))}
        </div>
      )}
    </div>
  )
}
