import { useEffect, useState } from 'react'
import { API_BASE_URL, fetchHealth, type HealthResponse } from './lib/api'

type Status =
  { kind: 'pending' } | { kind: 'ok'; data: HealthResponse } | { kind: 'error'; message: string }

function App() {
  const [status, setStatus] = useState<Status>({ kind: 'pending' })

  useEffect(() => {
    let cancelled = false

    fetchHealth()
      .then((data) => {
        if (!cancelled) setStatus({ kind: 'ok', data })
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setStatus({
            kind: 'error',
            message: error instanceof Error ? error.message : 'Unknown error',
          })
        }
      })

    return () => {
      cancelled = true
    }
  }, [])

  return (
    <main>
      <h1>Pollution Intelligence Platform</h1>
      <p>Project scaffold — pollution features are not implemented yet.</p>

      <div className="status-card">
        <div className="status-row">
          <span
            className={`status-dot ${status.kind === 'ok' ? 'ok' : status.kind === 'error' ? 'error' : 'pending'}`}
          />
          <strong>Backend health</strong>
        </div>
        <p>
          <code>{API_BASE_URL}/health</code>
        </p>

        {status.kind === 'pending' && <p>Checking…</p>}
        {status.kind === 'ok' && <pre>{JSON.stringify(status.data, null, 2)}</pre>}
        {status.kind === 'error' && (
          <p>
            Could not reach the backend: {status.message}. Is it running? See the README for startup
            instructions.
          </p>
        )}
      </div>
    </main>
  )
}

export default App
