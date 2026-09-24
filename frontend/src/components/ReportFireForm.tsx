import { useEffect, useRef, useState } from 'react'
import {
  ApiError,
  reportEvidencePhotoUrl,
  submitReport,
  submitReportEvidence,
  type UploadProgress,
} from '../lib/api'
import {
  CITIZEN_VERIFICATION_BADGE,
  CITIZEN_VERIFICATION_TOOLTIP,
  FIRE_KIND_LABELS,
  SMOKE_LABELS,
} from '../lib/citizenReports'
import {
  EVIDENCE_NEVER_A_MEASUREMENT,
  EVIDENCE_UPLOAD_DETAIL,
  VERIFICATION_DETAIL,
  VERIFICATION_LABELS,
  DEFAULT_SENSOR_UNIT,
  evidenceErrorSummary,
  explainEvidenceError,
  formatBytes,
  hasEvidenceErrors,
  isRetryableEvidenceError,
  sensorSummary,
  toEvidenceForm,
  validateEvidence,
  type EvidenceFieldErrors,
  type SensorEvidenceDraft,
} from '../lib/reportEvidence'
import {
  MAX_NOTES,
  errorSummary,
  hasErrors,
  toSubmitBody,
  validateFireReport,
} from '../lib/reportValidation'
import type { FireReportFieldErrors } from '../lib/reportValidation'
import {
  SENSOR_POLLUTANTS,
  type EvidenceVerificationStatus,
  type FireReportKind,
  type FireReportOut,
  type ReportEvidenceOut,
  type SensorPollutant,
} from '../lib/types'

/** Duration buckets, matching the app/back: the answer is fuzzy ("a couple
 *  of hours"), so the form offers buckets rather than a precise number. */
const DURATION_OPTIONS: { label: string; hours: number }[] = [
  { label: 'Just started', hours: 0 },
  { label: 'Under an hour', hours: 0.5 },
  { label: '1-3 hours', hours: 2 },
  { label: '3-6 hours', hours: 4.5 },
  { label: 'More than 6 hours', hours: 12 },
]

const SENSOR_UNITS = ['µg/m³', 'ppm', 'AQI', 'ppb', 'other'] as const

/** A failure of the evidence step, kept whole so the retry button can be
 *  honest about whether another attempt could succeed. */
interface EvidenceFailure {
  status: number
  code: string
  message: string
  retryable: boolean
}

/** RFC 3339 with an offset, which is what the backend requires. */
function toRfc3339(local: string): string {
  const parsed = new Date(local)
  return Number.isNaN(parsed.getTime()) ? '' : parsed.toISOString()
}

/** `datetime-local` wants `YYYY-MM-DDTHH:mm` in the viewer's own zone. */
function toLocalInputValue(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(
    date.getHours(),
  )}:${pad(date.getMinutes())}`
}

/**
 * Report a fire/burning event at the map's current centre, then attach evidence.
 *
 * Two steps against two endpoints, and the split is the whole point:
 *
 *  1. `POST /api/v1/reports` creates the report. One idempotency id is minted
 *     per open form and reused by every attempt, so retrying cannot stack a
 *     duplicate.
 *  2. `POST /api/v1/reports/{id}/evidence` attaches the selected photo and/or
 *     the local sensor reading. It is keyed by the id step 1 returned, and that
 *     id is kept for the rest of the form's life — so a failed evidence upload
 *     is retried against the report that already exists, never by filing a
 *     second report. The evidence call carries its own idempotency key, the
 *     same one on every retry, which is what makes the retry return the stored
 *     record instead of a second one.
 *
 * What the form refuses to imply:
 *
 *  - **A citizen reading is not a measurement.** It is stored on the evidence
 *    record only, never as a station observation, and the backend starts every
 *    record `unverified`. The confirmation says so and shows the status the
 *    server actually returned.
 *  - **The bytes decide, not the file extension.** A truncated transfer is
 *    refused by the server, so a retry after an interruption is a real
 *    possibility rather than a promise.
 *  - **A refusal is not always retryable.** A rejected photo or a bad reading
 *    fails identically next time, so the form says what to change instead of
 *    offering a retry that cannot work.
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

  // The report, once created. Its presence is what switches the form from
  // "create a report" to "attach evidence to report N".
  const [report, setReport] = useState<FireReportOut | null>(null)
  const [evidence, setEvidence] = useState<ReportEvidenceOut | null>(null)
  const [progress, setProgress] = useState<UploadProgress | null>(null)
  const [uploading, setUploading] = useState(false)
  const [evidenceFailure, setEvidenceFailure] = useState<EvidenceFailure | null>(null)
  const [evidenceErrors, setEvidenceErrors] = useState<EvidenceFieldErrors>({})
  const [evidenceAttempted, setEvidenceAttempted] = useState(false)

  const [photo, setPhoto] = useState<{ file: File; previewUrl: string } | null>(null)
  const [photoError, setPhotoError] = useState<string | null>(null)
  const [pollutant, setPollutant] = useState<SensorPollutant>('pm25')
  const [sensorValue, setSensorValue] = useState('')
  const [sensorUnit, setSensorUnit] = useState<string>(DEFAULT_SENSOR_UNIT)
  const [attachReading, setAttachReading] = useState(false)
  const [measuredAt, setMeasuredAt] = useState(() => toLocalInputValue(new Date()))

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

  // The evidence upload's own idempotency key, derived from the report's so a
  // single stable pair exists per form. Reusing the same value on every retry
  // is what turns a second attempt into "return the stored record" rather
  // than "store a second one".
  const evidenceClientId = (): string => `ev-${clientReportId()}`

  // Object URLs are a manual resource: the previous one is released whenever
  // the photo changes and when the form goes away.
  useEffect(() => {
    return () => {
      if (photo !== null) URL.revokeObjectURL(photo.previewUrl)
    }
  }, [photo])

  const parsedReading = Number.parseFloat(sensorValue)
  const readingValid =
    sensorValue.trim() !== '' && Number.isFinite(parsedReading) && parsedReading >= 0

  const sensorDraft = (): SensorEvidenceDraft | null => {
    if (!attachReading || !readingValid) return null
    const measuredAtRfc = toRfc3339(measuredAt)
    if (measuredAtRfc === '') return null
    return {
      pollutant,
      value: parsedReading,
      unit: sensorUnit,
      measuredAt: measuredAtRfc,
    }
  }

  const onPhotoPicked = (file: File | null) => {
    setPhotoError(null)
    if (file === null) {
      setPhoto(null)
      return
    }
    if (file.type !== '' && !file.type.startsWith('image/')) {
      setPhotoError('That file is not an image.')
      return
    }
    setPhoto({ file, previewUrl: URL.createObjectURL(file) })
  }

  const uploadEvidence = async (reportId: number) => {
    setEvidenceAttempted(true)
    setEvidenceFailure(null)
    setProgress(null)

    const draft = {
      photo: photo === null ? null : photo.file,
      sensor: sensorDraft(),
      notes,
    }
    const errors = validateEvidence(draft)
    setEvidenceErrors(errors)
    if (hasEvidenceErrors(errors)) {
      setEvidenceFailure(null)
      setMessage(evidenceErrorSummary(errors))
      return
    }
    setMessage(null)

    setUploading(true)
    try {
      const envelope = await submitReportEvidence(
        reportId,
        toEvidenceForm(draft, evidenceClientId()),
        setProgress,
      )
      setEvidence(envelope.data)
      setEvidenceFailure(null)
    } catch (error) {
      if (error instanceof ApiError) {
        setEvidenceFailure({
          status: error.status,
          code: error.code,
          message: error.message,
          retryable: isRetryableEvidenceError(error.status, error.code),
        })
      } else {
        setEvidenceFailure({
          status: 0,
          code: 'network_error',
          message: 'The upload did not complete.',
          retryable: true,
        })
      }
    } finally {
      setUploading(false)
      setProgress(null)
    }
  }

  const submit = async () => {
    if (sending) return
    setAttempted(true)
    setMessage(null)
    setFailed(false)

    const errors = validateFireReport({
      latitude,
      longitude,
      kind,
      smoke_intensity: intensity,
      duration_hours: durationHours,
      notes,
    })
    setFieldErrors(errors)
    if (hasErrors(errors)) {
      setFailed(true)
      setMessage(errorSummary(errors))
      return
    }

    setSending(true)
    try {
      const envelope = await submitReport(toSubmitBody(
        { latitude, longitude, kind, smoke_intensity: intensity, duration_hours: durationHours, notes },
        clientReportId(),
      ))
      const created = envelope.data
      setReport(created)
      onSubmitted()
      // The report exists now. Evidence is a second call against its id, and
      // nothing below this line can create another report.
      await uploadEvidence(created.id)
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

  // --- The report exists: the only thing left is the evidence upload ---
  if (report !== null) {
    const stored = evidence !== null
    const status: EvidenceVerificationStatus | null =
      evidence?.verification_status ?? null

    return (
      <section className="panel report-form" aria-label="Report a fire">
        <div className="report-form-header">
          <h3>{stored ? 'Report and evidence stored' : 'Report stored'}</h3>
          <button type="button" className="report-form-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>

        <p className="muted">
          The backend stored report <b>#{report.id}</b> and snapped it to H3 cell{' '}
          <span className="report-result-cell">{report.h3_cell}</span>. The model treats it as a
          source on its next update cycle — the map will not change immediately.
        </p>

        {stored ? (
          <div className="report-evidence-stored">
            <h4>Evidence stored on the server</h4>
            {evidence.media !== null && (
              <div className="report-evidence-photo-row">
                <img
                  className="report-evidence-photo"
                  src={reportEvidencePhotoUrl(evidence.media.url)}
                  alt="The photo stored with this report"
                />
                <dl className="report-result">
                  <dt>Photo</dt>
                  <dd>
                    {evidence.media.content_type} · {formatBytes(evidence.media.byte_size)}
                  </dd>
                  <dt>Stored as</dt>
                  <dd className="report-evidence-sha">{evidence.media.sha256}</dd>
                </dl>
              </div>
            )}
            {evidence.sensor !== null && (
              <dl className="report-result">
                <dt>Local reading</dt>
                <dd>{sensorSummary(evidence)}</dd>
                <dt>Provenance</dt>
                <dd>
                  source: {evidence.sensor.source} · verified: {String(evidence.sensor.verified)}
                </dd>
              </dl>
            )}
            {evidence.media === null && evidence.sensor === null && (
              <p className="muted">No photo or reading was attached.</p>
            )}
            <p className="report-verification" data-status={status ?? 'unverified'}>
              <b>{status === null ? 'Unverified' : VERIFICATION_LABELS[status]}</b>
              {status !== null && ` — ${VERIFICATION_DETAIL[status]}`}
            </p>
            <p className="muted">{EVIDENCE_NEVER_A_MEASUREMENT}</p>
            <p className="muted report-evidence-submitted">
              Stored {new Date(evidence.submitted_at).toLocaleString()} · evidence #{evidence.id}
            </p>
          </div>
        ) : (
          <>
            <fieldset className="report-local">
              <legend>Photo (optional)</legend>
              <p className="muted report-local-note">{EVIDENCE_UPLOAD_DETAIL}</p>
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
                    {photo.file.name} · {formatBytes(photo.file.size)}
                  </span>
                  <button
                    type="button"
                    className="report-photo-remove"
                    onClick={() => onPhotoPicked(null)}
                  >
                    Remove
                  </button>
                </div>
              )}
              {photoError !== null && (
                <span className="report-field-error" role="alert">
                  {photoError}
                </span>
              )}
              {evidenceAttempted && evidenceErrors.photo !== undefined && (
                <span className="report-field-error" role="alert">
                  {evidenceErrors.photo}
                </span>
              )}
            </fieldset>

            <fieldset className="report-local">
              <legend>Local sensor reading (optional)</legend>
              <p className="muted report-local-note">
                From a monitor you own. Stored as unverified evidence with its unit and your
                timestamp — never as a station observation.
              </p>
              <label className="report-reading-include">
                <input
                  type="checkbox"
                  checked={attachReading}
                  disabled={!readingValid}
                  onChange={(event) => setAttachReading(event.target.checked)}
                />
                Attach this reading to the report
              </label>
              {evidenceAttempted && evidenceErrors.sensor_value !== undefined && (
                <span className="report-field-error" role="alert">
                  {evidenceErrors.sensor_value}
                </span>
              )}
              {evidenceAttempted && evidenceErrors.sensor_measured_at !== undefined && (
                <span className="report-field-error" role="alert">
                  {evidenceErrors.sensor_measured_at}
                </span>
              )}
            </fieldset>

            {uploading && (
              <div className="report-evidence-progress" role="status" aria-live="polite">
                <p className="muted">
                  Uploading evidence to report #{report.id}…
                  {progress !== null && progress.fraction !== null
                    ? ` ${Math.round(progress.fraction * 100)}%`
                    : ''}
                </p>
                <div
                  className="report-evidence-bar"
                  role="progressbar"
                  aria-label="Evidence upload progress"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  {...(progress !== null && progress.fraction !== null
                    ? { 'aria-valuenow': Math.round(progress.fraction * 100) }
                    : {})}
                >
                  <div
                    className="report-evidence-bar-fill"
                    style={{
                      width:
                        progress !== null && progress.fraction !== null
                          ? `${Math.round(progress.fraction * 100)}%`
                          : '35%',
                    }}
                  />
                </div>
              </div>
            )}

            {evidenceFailure !== null && (
              <div className="report-evidence-error" role="alert">
                <p className="report-form-error">
                  Evidence was not stored ({evidenceFailure.code}
                  {evidenceFailure.status > 0 ? `, HTTP ${evidenceFailure.status}` : ''}).
                </p>
                <p className="muted">{evidenceFailure.message}</p>
                <p className="muted">{explainEvidenceError(evidenceFailure.status, evidenceFailure.code)}</p>
                <p className="muted report-retry-note">
                  Report #{report.id} is already stored. Retrying uploads the evidence to that same
                  report — it cannot create a second one.
                </p>
                <button
                  type="button"
                  className="report-form-submit"
                  disabled={uploading}
                  onClick={() => uploadEvidence(report.id)}
                >
                  {evidenceFailure.retryable
                    ? 'Retry evidence upload'
                    : 'Send it again (after changing it above)'}
                </button>
              </div>
            )}
          </>
        )}

        <button type="button" className="report-form-submit" onClick={onClose}>
          Done
        </button>
      </section>
    )
  }

  // --- Form: nothing has been created yet ---
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
        <p className="muted report-local-note">{EVIDENCE_UPLOAD_DETAIL}</p>
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
              {photo.file.name} · {formatBytes(photo.file.size)}
            </span>
            <button
              type="button"
              className="report-photo-remove"
              onClick={() => onPhotoPicked(null)}
            >
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
          From a monitor you own. Stored as unverified evidence with its unit and your timestamp —
          never as a station observation.
        </p>
        <label className="report-reading-include">
          <input
            type="checkbox"
            checked={attachReading}
            disabled={!readingValid}
            onChange={(event) => setAttachReading(event.target.checked)}
          />
          Attach this reading to the report
        </label>
            <div className="report-reading-row">
              <select
                value={pollutant}
                onChange={(event) => setPollutant(event.target.value as SensorPollutant)}
                aria-label="Pollutant"
              >
                {SENSOR_POLLUTANTS.map((value) => (
                  <option key={value} value={value}>
                    {value === 'pm25' ? 'PM2.5' : 'PM10'}
                  </option>
                ))}
              </select>
              <input
                type="number"
                min={0}
                step="any"
                inputMode="decimal"
                className="report-reading-value"
                placeholder="e.g. 87.5"
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
            <label className="report-field">
              <span>Reading taken at</span>
              <input
                type="datetime-local"
                value={measuredAt}
                onChange={(event) => setMeasuredAt(event.target.value)}
              />
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
