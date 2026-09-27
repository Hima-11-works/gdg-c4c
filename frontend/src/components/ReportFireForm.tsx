import { useEffect, useRef, useState } from 'react'
import { ApiError, fetchReportStatus, submitReport } from '../lib/api'
import { FIRE_KIND_LABELS, REPORT_STATUS_LABELS, SMOKE_LABELS } from '../lib/citizenReports'
import {
  EvidenceError,
  attachPhoto,
  describePhotoProblem,
  isMediaUnavailable,
  type EvidenceOut,
} from '../lib/evidence'
import type { FireReportKind, FireReportWithStatus } from '../lib/types'

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
  // After a successful submit the form shows the report's standing instead of
  // the fields: the citizen asked "did you get my fire?", and the honest answer
  // is a status, not a confirmation toast that implies it counted.
  const [submitted, setSubmitted] = useState<FireReportWithStatus | null>(null)
  const [refreshing, setRefreshing] = useState(false)

  // --- F2: the optional photo -------------------------------------------
  // The report is submitted first and the photo second, deliberately. A photo
  // is evidence for a report, not a condition of one, so nothing here can
  // prevent the report itself from being filed - which is F2's rule that a
  // report must succeed without a photo.
  const [photo, setPhoto] = useState<File | null>(null)
  const [photoPreview, setPhotoPreview] = useState<string | null>(null)
  const [photoProblem, setPhotoProblem] = useState<string | null>(null)
  const [consent, setConsent] = useState(false)
  const [photoProgress, setPhotoProgress] = useState<number | null>(null)
  const [photoError, setPhotoError] = useState<string | null>(null)
  const [evidence, setEvidence] = useState<EvidenceOut | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  // The live object URL, held in a ref so unmount can revoke it without
  // touching state.
  const previewUrlRef = useRef<string | null>(null)

  // Object URLs leak until revoked, and this form can pick, replace and remove
  // a photo several times in one sitting. The URL is created and revoked in the
  // events that cause it to change - `pickPhoto` and `removePhoto` - and this
  // effect only covers the unmount case, which is the one an event cannot.
  useEffect(
    () => () => {
      if (previewUrlRef.current !== null) URL.revokeObjectURL(previewUrlRef.current)
    },
    [],
  )

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
      const created = await submitReport({
        latitude,
        longitude,
        kind,
        smoke_intensity: intensity,
        duration_hours: durationHours,
        ...(trimmedNotes === '' ? {} : { notes: trimmedNotes }),
        client_report_id: clientReportId(),
      })
      onSubmitted()
      // The POST response is the unchanged v1 shape (ten submission fields), so
      // the standing comes from the detail read. A failure there must not lose
      // the report: it was accepted, so the id is all we show.
      try {
        const detail = await fetchReportStatus(created.data.id)
        setSubmitted(detail.data)
      } catch {
        setSubmitted({
          ...created.data,
          status: 'submitted',
          status_meaning: 'received and waiting for review',
          is_verified: false,
          affects_air_quality_model: false,
          last_status_change_at: null,
          expires_at: null,
          seconds_until_expiry: null,
          corroborating_report_count: 0,
          cluster_id: null,
          evidence_count: 0,
          evidence_expected: false,
        })
        setMessage('Report received. Its status is not available right now.')
      }

      // The report exists now, so the photo can be attached. Anything that goes
      // wrong from here is reported as a photo problem and never un-sends the
      // report: the honest failure is "your fire was filed, the photo did not
      // make it", and the form offers a retry rather than a resubmit.
      if (photo !== null) {
        try {
          setPhotoProgress(0)
          setPhotoError(null)
          const attached = await attachPhoto({
            reportId: created.data.id,
            file: photo,
            consent,
            onProgress: setPhotoProgress,
          })
          setEvidence(attached.data)
          clearPhoto()
        } catch (error) {
          if (isMediaUnavailable(error)) {
            setPhotoError(
              'This deployment is not accepting photos. Your report was filed without one.',
            )
          } else if (error instanceof EvidenceError) {
            setPhotoError(error.message)
          } else {
            setPhotoError('The photo did not upload. Your report was filed without it.')
          }
        } finally {
          setPhotoProgress(null)
        }
      }
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

  /** Drop the current photo and release its preview URL. */
  const clearPhoto = () => {
    if (previewUrlRef.current !== null) {
      URL.revokeObjectURL(previewUrlRef.current)
      previewUrlRef.current = null
    }
    setPhoto(null)
    setPhotoPreview(null)
  }

  const pickPhoto = (file: File | null) => {
    setPhotoError(null)
    if (file === null) {
      clearPhoto()
      return
    }
    const problem = describePhotoProblem(file)
    clearPhoto()
    if (problem !== null && problem.includes('The limit')) {
      // A hard local refusal: there is no point spending the upload. Anything
      // softer is only a hint, because the server decides from the bytes.
      setPhotoProblem(problem)
      return
    }
    setPhotoProblem(problem)
    const url = URL.createObjectURL(file)
    previewUrlRef.current = url
    setPhotoPreview(url)
    setPhoto(file)
  }

  const removePhoto = () => {
    setPhotoProblem(null)
    setPhotoError(null)
    clearPhoto()
    if (fileInputRef.current !== null) fileInputRef.current.value = ''
  }

  /** Re-send just the photo against the report that already exists. */
  const retryPhoto = async () => {
    if (submitted === null || photo === null) return
    try {
      setPhotoProgress(0)
      setPhotoError(null)
      const attached = await attachPhoto({
        reportId: submitted.id,
        file: photo,
        consent,
        onProgress: setPhotoProgress,
      })
      setEvidence(attached.data)
      clearPhoto()
    } catch (error) {
      setPhotoError(
        error instanceof EvidenceError ? error.message : 'The photo did not upload.',
      )
    } finally {
      setPhotoProgress(null)
    }
  }

  const refreshStatus = async () => {
    if (submitted === null) return
    setRefreshing(true)
    try {
      const detail = await fetchReportStatus(submitted.id)
      setSubmitted(detail.data)
    } catch {
      setMessage('Could not refresh the status. Try again in a moment.')
    } finally {
      setRefreshing(false)
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
        At {latitude.toFixed(4)}, {longitude.toFixed(4)} (map centre). Your report is filed as an
        unverified claim: a reviewer has to corroborate it before it affects the air-quality model.
      </p>

      {submitted ? (
        <div className="report-status" role="status">
          <h4>Report #{submitted.id} received</h4>
          <p>
            <b>{REPORT_STATUS_LABELS[submitted.status] ?? submitted.status}</b> —{' '}
            {submitted.status_meaning}
          </p>
          <p className="muted">
            {submitted.affects_air_quality_model
              ? 'This report is currently contributing to the modeled air quality near the reported location.'
              : 'This report is not affecting the air-quality model yet.'}
          </p>
          <dl className="report-status-facts">
            <dt>Submitted</dt>
            <dd>{new Date(submitted.reported_at).toLocaleString()}</dd>
            {submitted.expires_at && (
              <>
                <dt>Actionable until</dt>
                <dd>{new Date(submitted.expires_at).toLocaleString()}</dd>
              </>
            )}
            <dt>Corroborating reports</dt>
            <dd>{submitted.corroborating_report_count}</dd>
            {submitted.evidence_count > 0 && (
              <>
                <dt>Evidence attached</dt>
                <dd>{submitted.evidence_count}</dd>
              </>
            )}
          </dl>
          <button type="button" onClick={refreshStatus} disabled={refreshing}>
            {refreshing ? 'Checking…' : 'Check for an update'}
          </button>
        </div>
      ) : (
        <>
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

      {/* F2: the photo is optional throughout. Nothing here is required for
          the report to be filed, and the labels say so, because a citizen who
          declines to photograph a fire should never be told they have not
          finished reporting it. */}
      <div className="report-photo">
        <span className="report-photo-label">Add a photo (optional)</span>

        {photo === null ? (
          <label className="report-photo-pick">
            <input
              ref={fileInputRef}
              type="file"
              accept="image/jpeg,image/png,image/webp"
              capture="environment"
              onChange={(event) => pickPhoto(event.target.files?.[0] ?? null)}
            />
            <span>Choose a photo…</span>
          </label>
        ) : (
          <div className="report-photo-chosen">
            {photoPreview !== null && (
              <img className="report-photo-preview" src={photoPreview} alt="" />
            )}
            <span className="report-photo-name">{photo.name}</span>
            <span className="muted">{(photo.size / 1024).toFixed(0)} KB</span>
            <button type="button" onClick={removePhoto} disabled={photoProgress !== null}>
              Remove
            </button>
          </div>
        )}

        {photoProgress !== null && (
          <progress className="report-photo-progress" value={photoProgress} max={1} />
        )}

        {photoProblem !== null && (
          <p className="muted report-photo-hint">
            {photoProblem}
          </p>
        )}

        <label className="report-photo-consent">
          <input
            type="checkbox"
            checked={consent}
            onChange={(event) => setConsent(event.target.checked)}
          />
          <span>
            I took this photo and agree to it being reviewed. Location data
            embedded in the picture is removed before anyone sees it, and the
            photo is deleted after the review window closes.
          </span>
        </label>
      </div>

      {/* After a submission the photo has either landed or failed, and this is
          where that is said. A retry here re-sends only the photo - the report
          is already stored, so this must never look like a second report. */}
      {photoError !== null && submitted !== null && (
        <div className="report-photo-retry">
          <p className="report-form-error" role="status">
            {photoError}
          </p>
          {photo !== null && (
            <button type="button" onClick={retryPhoto} disabled={photoProgress !== null}>
              {photoProgress !== null ? 'Retrying…' : 'Retry photo'}
            </button>
          )}
        </div>
      )}
      {evidence !== null && (
        <p className="muted" role="status">
          Photo attached (evidence #{evidence.id}). It is not counted towards the
          model until a reviewer looks at it.
          {evidence.scan_state === 'quarantined'
            ? ' It could not be read as an image, so it is being held rather than shown.'
            : ''}
        </p>
      )}

      {message !== null && (
        <p className={failed ? 'report-form-error' : 'muted'} role="status">
          {message}
        </p>
      )}

      <button type="button" className="report-form-submit" onClick={submit} disabled={sending}>
        {sending ? 'Sending…' : 'Submit report'}
      </button>
        </>
      )}
    </section>
  )
}

