import { useRef, useState } from 'react'
import { ApiError, submitReport } from '../lib/api'
import { FIRE_KIND_LABELS, SMOKE_LABELS } from '../lib/citizenReports'
import type { FireReportKind } from '../lib/types'

/** Duration buckets, matching the app/back: the answer is fuzzy ("a couple
 *  of hours"), so the form offers buckets rather than a precise number. */
const DURATION_OPTIONS: { label: string; hours: number }[] = [
  { label: 'Just started', hours: 0 },
  { label: 'Under an hour', hours: 0.5 },
  { label: '1-3 hours', hours: 2 },
  { label: '3-6 hours', hours: 4.5 },
  { label: 'More than 6 hours', hours: 12 },
]

const MAX_NOTES = 280

/**
 * Report a fire/burning event at the map's current centre.
 *
 * The only write side in the dashboard: POST /api/v1/reports stores the
 * report, and the backend's fire gradient model turns active reports into a
 * modeled plume at its next pipeline run. The form is explicit about that
 * lag rather than implying the map changes instantly.
 *
 * Validation mirrors the backend's bounds, so a mistyped submission fails
 * here with a clear message instead of a 422.
 */
export function ReportFireForm({
  latitude,
  longitude,
  onClose,
  onSubmitted,
}: {
  latitude: number
  longitude: number
  onClose: () => void
  onSubmitted: () => void
}) {
  const [kind, setKind] = useState<FireReportKind>('other')
  const [intensity, setIntensity] = useState(3)
  const [durationHours, setDurationHours] = useState(DURATION_OPTIONS[0].hours)
  const [notes, setNotes] = useState('')
  const [sending, setSending] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)

  // One idempotency id per open form, minted on first submit (an event
  // handler, so the render stays pure): retrying after a timeout resubmits
  // the same id, so the backend cannot stack a duplicate report.
  const clientIdRef = useRef<string | null>(null)
  const clientReportId = (): string => {
    if (clientIdRef.current === null) {
      clientIdRef.current = `web-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
    }
    return clientIdRef.current
  }

  const submit = async () => {
    if (sending) return
    const trimmedNotes = notes.trim()
    if (trimmedNotes.length > MAX_NOTES) {
      setFailed(true)
      setMessage(`Notes must be at most ${MAX_NOTES} characters.`)
      return
    }

    setSending(true)
    setMessage(null)
    setFailed(false)
    try {
      await submitReport({
        latitude,
        longitude,
        kind,
        smoke_intensity: intensity,
        duration_hours: durationHours,
        ...(trimmedNotes === '' ? {} : { notes: trimmedNotes }),
        client_report_id: clientReportId(),
      })
      onSubmitted()
      onClose()
    } catch (error) {
      setFailed(true)
      setMessage(
        error instanceof ApiError
          ? `Could not send the report: ${error.message}`
          : 'Could not send the report. Check your connection and try again.',
      )
    } finally {
      setSending(false)
    }
  }

  return (
    <section className="panel report-form" aria-label="Report a fire">
      <div className="report-form-header">
        <h3>Report a fire</h3>
        <button type="button" className="report-form-close" onClick={onClose} aria-label="Close">
          ×
        </button>
      </div>
      <p className="muted">
        At {latitude.toFixed(4)}, {longitude.toFixed(4)} (map centre). The model treats an
        active report as a source here and picks it up on its next update cycle.
      </p>

      <label className="report-field">
        <span>What is burning?</span>
        <select value={kind} onChange={(event) => setKind(event.target.value as FireReportKind)}>
          {(Object.keys(FIRE_KIND_LABELS) as FireReportKind[]).map((value) => (
            <option key={value} value={value}>
              {FIRE_KIND_LABELS[value]}
            </option>
          ))}
        </select>
      </label>

      <label className="report-field">
        <span>
          How much smoke? <b>{SMOKE_LABELS[intensity - 1]}</b>
        </span>
        <input
          type="range"
          min={1}
          max={5}
          step={1}
          value={intensity}
          onChange={(event) => setIntensity(Number(event.target.value))}
        />
      </label>

      <label className="report-field">
        <span>How long has it been going?</span>
        <select
          value={durationHours}
          onChange={(event) => setDurationHours(Number(event.target.value))}
        >
          {DURATION_OPTIONS.map((option) => (
            <option key={option.label} value={option.hours}>
              {option.label}
            </option>
          ))}
        </select>
      </label>

      <label className="report-field">
        <span>Anything else? (optional)</span>
        <textarea
          rows={2}
          maxLength={MAX_NOTES}
          value={notes}
          placeholder="e.g. crop stubble, smoke drifting toward the road"
          onChange={(event) => setNotes(event.target.value)}
        />
      </label>

      {message !== null && (
        <p className={failed ? 'report-form-error' : 'muted'} role="status">
          {message}
        </p>
      )}

      <button type="button" className="report-form-submit" onClick={submit} disabled={sending}>
        {sending ? 'Sending…' : 'Submit report'}
      </button>
    </section>
  )
}
