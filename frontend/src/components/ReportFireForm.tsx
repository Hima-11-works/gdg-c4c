import { useEffect, useRef, useState } from 'react'
import { ApiError, submitReport } from '../lib/api'
import {
  CITIZEN_VERIFICATION_BADGE,
  CITIZEN_VERIFICATION_TOOLTIP,
  FIRE_KIND_LABELS,
  LOCAL_ONLY_DETAIL,
  SMOKE_LABELS,
} from '../lib/citizenReports'
import {
  MAX_NOTES,
  errorSummary,
  hasErrors,
  toSubmitBody,
  validateFireReport,
} from '../lib/reportValidation'
import type { FireReportFieldErrors } from '../lib/reportValidation'
import type { FireReportKind, FireReportOut } from '../lib/types'

/** Duration buckets, matching the app/back: the answer is fuzzy ("a couple
 *  of hours"), so the form offers buckets rather than a precise number. */
const DURATION_OPTIONS: { label: string; hours: number }[] = [
  { label: 'Just started', hours: 0 },
  { label: 'Under an hour', hours: 0.5 },
  { label: '1-3 hours', hours: 2 },
  { label: '3-6 hours', hours: 4.5 },
  { label: 'More than 6 hours', hours: 12 },
]

const SENSOR_UNITS = ['µg/m³', 'ppm', 'AQI', 'other'] as const

/** A photo the resident attached, held in memory only. */
interface LocalPhoto {
  name: string
  size: number
  previewUrl: string
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/**
 * Report a fire/burning event at the map's current centre.
 *
 * The only write side in the dashboard: POST /api/v1/reports stores the
 * report, and the backend's fire gradient model turns active reports into a
 * modeled plume at its next pipeline run. The form is explicit about that
 * lag rather than implying the map changes instantly.
 *
 * Three things this form is careful to be honest about:
 *
 *  - **Validation** mirrors the backend's bounds (lib/reportValidation), so a
 *    mistyped submission fails here, at the field, instead of as a 422.
 *  - **Retry is safe.** One idempotency id is minted per open form and reused
 *    by every attempt, so a retry after a timeout returns the original stored
 *    row instead of stacking a duplicate. The UI says so rather than leaving
 *    the user to hope.
 *  - **The photo and the local reading are not sent.** The backend has no
 *    endpoint that accepts either (POST /api/v1/reports takes no image and no
 *    sensor value, and there is no sensor write route at all), so they stay on
 *    this device and are labelled as such. The one thing the resident can
 *    choose to transmit is the reading *as text in the note*, and only by
 *    ticking a box that says exactly what will be sent.
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
  const [fieldErrors, setFieldErrors] = useState<FireReportFieldErrors>({})
  const [attempted, setAttempted] = useState(false)
  const [submitted, setSubmitted] = useState<FireReportOut | null>(null)

  // Local-only extras: never part of the request body.
  const [photo, setPhoto] = useState<LocalPhoto | null>(null)
  const [photoError, setPhotoError] = useState<string | null>(null)
  const [sensorValue, setSensorValue] = useState('')
  const [sensorUnit, setSensorUnit] = useState<string>(SENSOR_UNITS[0])
  const [includeReading, setIncludeReading] = useState(false)

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

  // Object URLs are a manual resource: release the previous one whenever the
  // photo changes and when the form goes away.
  useEffect(() => {
    return () => {
      if (photo !== null) URL.revokeObjectURL(photo.previewUrl)
    }
  }, [photo])

  const parsedReading = Number.parseFloat(sensorValue)
  const readingValid =
    sensorValue.trim() !== '' && Number.isFinite(parsedReading) && parsedReading >= 0

  /** The note text that will actually be sent: the resident's note, plus the
   *  reading only if they asked for it, labelled so the stored text says what
   *  it is. */
  const outgoingNotes = (): string => {
    const base = notes.trim()
    if (!includeReading) return base
    const line = `Self-measured reading: ${parsedReading} ${sensorUnit} (${CITIZEN_VERIFICATION_BADGE})`
    return base === '' ? line : `${base}\n${line}`
  }

  const onPhotoPicked = (file: File | null) => {
    setPhotoError(null)
    if (file === null) {
      setPhoto(null)
      return
    }
    if (!file.type.startsWith('image/')) {
      setPhotoError('That file is not an image.')
      return
    }
    setPhoto({ name: file.name, size: file.size, previewUrl: URL.createObjectURL(file) })
  }

  const submit = async () => {
    if (sending) return
    setAttempted(true)
    setMessage(null)
    setFailed(false)

    const outgoing = outgoingNotes()
    const errors = validateFireReport({
      latitude,
      longitude,
      kind,
      smoke_intensity: intensity,
      duration_hours: durationHours,
      notes: outgoing,
    })
    if (includeReading && !readingValid) {
      errors.notes = 'Enter a reading of 0 or more, or untick "include in the note".'
    }
    setFieldErrors(errors)
    if (hasErrors(errors)) {
      setFailed(true)
      setMessage(errorSummary(errors))
      return
    }

    setSending(true)
    try {
      const envelope = await submitReport(toSubmitBody(
        { latitude, longitude, kind, smoke_intensity: intensity, duration_hours: durationHours, notes: outgoing },
        clientReportId(),
      ))
      onSubmitted()
      setSubmitted(envelope.data)
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

  // --- Confirmation: the stored record, exactly as the backend returned it ---
  if (submitted !== null) {
    return (
      <section className="panel report-form" aria-label="Report a fire">
        <div className="report-form-header">
          <h3>Report stored</h3>
          <button type="button" className="report-form-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>
        <p className="muted">
          The backend stored this report and snapped it to the H3 cell below. The model picks
          it up as a source on its next update cycle — the map will not change immediately.
        </p>
        <dl className="report-result">
          <dt>Report id</dt>
          <dd>{submitted.id}</dd>
          <dt>H3 cell</dt>
          <dd className="report-result-cell">{submitted.h3_cell}</dd>
          <dt>What</dt>
          <dd>{FIRE_KIND_LABELS[submitted.kind] ?? submitted.kind}</dd>
          <dt>Smoke</dt>
          <dd>
            {SMOKE_LABELS[submitted.smoke_intensity - 1] ?? submitted.smoke_intensity} (
            {submitted.smoke_intensity}/5)
          </dd>
          <dt>Duration</dt>
          <dd>{submitted.duration_hours === 0 ? 'just started' : `~${submitted.duration_hours}h`}</dd>
          <dt>Stored at</dt>
          <dd>{new Date(submitted.reported_at).toLocaleString()}</dd>
          {submitted.notes !== null && submitted.notes !== '' && (
            <>
              <dt>Note sent</dt>
              <dd className="report-result-note">{submitted.notes}</dd>
            </>
          )}
        </dl>
        <p className="report-unverified" title={CITIZEN_VERIFICATION_TOOLTIP}>
          {CITIZEN_VERIFICATION_BADGE}
        </p>
        {(photo !== null || sensorValue.trim() !== '') && (
          <p className="muted report-local-only">
            {LOCAL_ONLY_DETAIL}
            {photo !== null && ` Photo: ${photo.name} (${formatBytes(photo.size)}).`}
            {sensorValue.trim() !== '' &&
              ` Reading: ${sensorValue} ${sensorUnit}${
                includeReading ? ' (also sent in the note above)' : ' (not sent)'
              }.`}
          </p>
        )}
        <button type="button" className="report-form-submit" onClick={onClose}>
          Done
        </button>
      </section>
    )
  }

  // --- Form ---
  // `attempted` (state, not the ref) is what says an attempt has been made:
  // reading clientIdRef.current during render would be a ref read in render,
  // and it is true exactly when a submit has run at least once.
  const retrying = failed && attempted
  const errorFor = (field: keyof FireReportFieldErrors) =>
    attempted && fieldErrors[field] !== undefined ? (
      <span className="report-field-error" role="alert">
        {fieldErrors[field]}
      </span>
    ) : null

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
        {errorFor('smoke_intensity')}
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
        {errorFor('duration_hours')}
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
        {errorFor('notes')}
      </label>

      <fieldset className="report-local">
        <legend>Photo (optional)</legend>
        <p className="muted report-local-note">{LOCAL_ONLY_DETAIL}</p>
        <input
          type="file"
          accept="image/*"
          capture="environment"
          onChange={(event) => onPhotoPicked(event.target.files?.[0] ?? null)}
        />
        {photo !== null && (
          <div className="report-photo">
            <img src={photo.previewUrl} alt="" className="report-photo-preview" />
            <span className="muted">
              {photo.name} · {formatBytes(photo.size)}
            </span>
            <button type="button" className="report-photo-remove" onClick={() => onPhotoPicked(null)}>
              Remove
            </button>
          </div>
        )}
        {photoError !== null && (
          <span className="report-field-error" role="alert">
            {photoError}
          </span>
        )}
      </fieldset>

      <fieldset className="report-local">
        <legend>Local sensor reading (optional)</legend>
        <p className="muted report-local-note">
          If you have a monitor at home, you can note its reading. {LOCAL_ONLY_DETAIL}
        </p>
        <div className="report-reading-row">
          <input
            type="number"
            min={0}
            step="any"
            inputMode="decimal"
            className="report-reading-value"
            placeholder="e.g. 145"
            value={sensorValue}
            onChange={(event) => setSensorValue(event.target.value)}
          />
          <select
            value={sensorUnit}
            onChange={(event) => setSensorUnit(event.target.value)}
            aria-label="Reading unit"
          >
            {SENSOR_UNITS.map((unit) => (
              <option key={unit} value={unit}>
                {unit}
              </option>
            ))}
          </select>
        </div>
        <label className="report-reading-include">
          <input
            type="checkbox"
            checked={includeReading}
            disabled={!readingValid}
            onChange={(event) => setIncludeReading(event.target.checked)}
          />
          Also include this reading in the note text sent to the server — it will be stored as
          free text, labelled self-measured and unverified
        </label>
      </fieldset>

      {message !== null && (
        <p className={failed ? 'report-form-error' : 'muted'} role="status">
          {message}
        </p>
      )}

      {retrying && (
        <p className="muted report-retry-note">
          Retrying reuses the same report id, so it cannot create a duplicate — if the first
          attempt did reach the server, this returns the report it already stored.
        </p>
      )}

      {/* Said before submission, not only after: a resident should know how
          their report will be treated, not discover it in the confirmation. */}
      <p className="report-unverified" title={CITIZEN_VERIFICATION_TOOLTIP}>
        {CITIZEN_VERIFICATION_BADGE}
      </p>

      <button type="button" className="report-form-submit" onClick={submit} disabled={sending}>
        {sending ? 'Sending report…' : retrying ? 'Retry sending' : 'Submit report'}
      </button>
    </section>
  )
}
