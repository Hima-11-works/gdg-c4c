// Citizen intake evidence: the photo and the local sensor reading a resident
// attaches to a report they have already created (POST
// /api/v1/reports/{id}/evidence, see docs/api/citizen-intake.md).
//
// This module is the client-side half of that contract and nothing more: it
// builds the multipart field set, mirrors the documented bounds so an
// unusable reading fails next to the field that caused it, and classifies the
// server's error codes so the UI knows whether a retry can possibly help. The
// backend remains the authority on every bound - the checks here exist to fail
// faster, exactly like lib/reportValidation.ts does for the report itself.

import {
  SENSOR_POLLUTANTS,
  type EvidenceVerificationStatus,
  type ReportEvidenceOut,
  type SensorPollutant,
} from './types'

/** Mirrors CITIZEN_MEDIA_MAX_BYTES' default. Advisory only: a deployment may
 *  configure a different cap, and the server is what actually enforces it. */
export const DEFAULT_MAX_PHOTO_BYTES = 5 * 1024 * 1024

/** Mirrors CITIZEN_MEDIA_ALLOWED_TYPES' default. */
export const DEFAULT_ALLOWED_PHOTO_TYPES = ['image/jpeg', 'image/png', 'image/webp'] as const

/** Mirrors CITIZEN_SENSOR_MAX_AGE_HOURS. */
export const MAX_SENSOR_AGE_HOURS = 72

/** Mirrors CITIZEN_SENSOR_MAX_FUTURE_SKEW_SECONDS. */
export const MAX_SENSOR_FUTURE_SKEW_SECONDS = 300

export const SENSOR_POLLUTANT_LABELS: Record<SensorPollutant, string> = {
  pm25: 'PM2.5',
  pm10: 'PM10',
}

/** The default unit a home monitor reports a concentration in. The backend
 *  stores the unit it is given, so it is sent as typed rather than normalised. */
export const DEFAULT_SENSOR_UNIT = 'µg/m³'

/** A reading the resident is about to attach. All four parts travel together:
 *  the backend requires them as a set. */
export interface SensorEvidenceDraft {
  pollutant: SensorPollutant
  value: number
  unit: string
  measuredAt: string
}

/** Everything the resident selected for this report. Either part may be absent;
 *  at least one must be present or the backend refuses the request. */
export interface EvidenceDraft {
  photo: File | null
  sensor: SensorEvidenceDraft | null
  notes: string
}

export type EvidenceFieldError =
  | 'photo'
  | 'sensor_pollutant'
  | 'sensor_value'
  | 'sensor_unit'
  | 'sensor_measured_at'

export type EvidenceFieldErrors = Partial<Record<EvidenceFieldError, string>>

export function hasEvidenceErrors(errors: EvidenceFieldErrors): boolean {
  return Object.keys(errors).length > 0
}

/**
 * Check a draft against the documented evidence bounds.
 *
 * A photo is only checked for the two things that are knowable before the
 * bytes are sent - the declared type against the allow-list (a generic
 * `application/octet-stream` is accepted, because the backend normalises it
 * rather than punishing it) and the size against the cap. Whether the bytes
 * are a *complete* image of an accepted format is decided by the server, and
 * is deliberately not guessed at here: a truncated transfer is exactly the
 * case this check cannot see.
 */
export function validateEvidence(
  draft: EvidenceDraft,
  now: number = Date.now(),
): EvidenceFieldErrors {
  const errors: EvidenceFieldErrors = {}

  if (draft.photo === null && draft.sensor === null) {
    errors.photo = 'Attach a photo, a sensor reading, or both.'
  }

  if (draft.photo !== null) {
    const type = draft.photo.type.toLowerCase()
    const generic =
      type === '' ||
      type === 'application/octet-stream' ||
      type === 'binary/octet-stream' ||
      type === '*/*'
    if (!generic && !(DEFAULT_ALLOWED_PHOTO_TYPES as readonly string[]).includes(type)) {
      errors.photo = `The backend accepts ${DEFAULT_ALLOWED_PHOTO_TYPES.join(', ')}.`
    }
    if (draft.photo.size > DEFAULT_MAX_PHOTO_BYTES) {
      errors.photo = `The photo is ${formatBytes(draft.photo.size)}; the cap is ${formatBytes(
        DEFAULT_MAX_PHOTO_BYTES,
      )}.`
    }
  }

  const sensor = draft.sensor
  if (sensor !== null) {
    if (!(SENSOR_POLLUTANTS as readonly string[]).includes(sensor.pollutant)) {
      errors.sensor_pollutant = `The backend accepts ${SENSOR_POLLUTANTS.join(', ')}.`
    }
    if (!Number.isFinite(sensor.value) || sensor.value < 0) {
      errors.sensor_value = 'Enter a reading of 0 or more.'
    }
    if (sensor.unit.trim() === '') {
      errors.sensor_unit = 'A reading needs a unit.'
    }
    const measuredAt = Date.parse(sensor.measuredAt)
    if (Number.isNaN(measuredAt)) {
      errors.sensor_measured_at = 'The reading needs a time it was measured at.'
    } else {
      const ageSeconds = (now - measuredAt) / 1000
      if (ageSeconds > MAX_SENSOR_AGE_HOURS * 3600) {
        errors.sensor_measured_at = `A reading older than ${MAX_SENSOR_AGE_HOURS}h is refused as stale.`
      }
      if (ageSeconds < -MAX_SENSOR_FUTURE_SKEW_SECONDS) {
        errors.sensor_measured_at = 'That time is in the future. Check the device clock.'
      }
    }
  }

  return errors
}

/** "Please fix: photo." - one line for the form summary. */
export function evidenceErrorSummary(errors: EvidenceFieldErrors): string {
  const labels: Record<EvidenceFieldError, string> = {
    photo: 'the photo',
    sensor_pollutant: 'the pollutant',
    sensor_value: 'the reading',
    sensor_unit: 'the unit',
    sensor_measured_at: 'the time measured',
  }
  const fields = Object.keys(errors) as EvidenceFieldError[]
  if (fields.length === 0) return ''
  return `Please fix ${fields.map((field) => labels[field]).join(' and ')}.`
}

/**
 * Build the multipart body for one evidence upload.
 *
 * `clientReportId` is the idempotency key for the *evidence* record, and it
 * must be the same value on every retry of the same upload: identical payload
 * returns the stored record, a different payload under the same key is a 409.
 * That is what lets an interrupted upload be retried without producing a
 * second record.
 */
export function toEvidenceForm(
  draft: EvidenceDraft,
  clientReportId: string,
): FormData {
  const form = new FormData()
  if (draft.photo !== null) {
    form.append('photo', draft.photo, draft.photo.name)
  }
  form.append('client_report_id', clientReportId)
  const sensor = draft.sensor
  if (sensor !== null) {
    form.append('sensor_pollutant', sensor.pollutant)
    form.append('sensor_value', String(sensor.value))
    form.append('sensor_unit', sensor.unit)
    form.append('sensor_measured_at', sensor.measuredAt)
  }
  const notes = draft.notes.trim()
  if (notes !== '') form.append('notes', notes)
  return form
}

/** Verification state as the UI says it. `unverified` is the only state a fresh
 *  upload can be in, but the record can be moderated later, so all four are
 *  rendered rather than hard-coded. */
export const VERIFICATION_LABELS: Record<EvidenceVerificationStatus, string> = {
  unverified: 'Unverified',
  pending: 'Pending review',
  verified: 'Verified',
  rejected: 'Rejected',
}

export const VERIFICATION_DETAIL: Record<EvidenceVerificationStatus, string> = {
  unverified: 'stored as submitted; nothing has checked it',
  pending: 'queued for a moderator to review',
  verified: 'a moderator confirmed this evidence',
  rejected: 'a moderator rejected this evidence',
}

/** The one sentence that is true of every evidence record regardless of its
 *  state: a citizen reading is never a trusted station observation. */
export const EVIDENCE_NEVER_A_MEASUREMENT =
  'A citizen reading is stored only on this evidence record. It never becomes a station ' +
  'observation and never feeds the pollution model.'

/** What the form says before the upload, replacing the old "kept on this
 *  device" wording: both parts are now transmitted, and this is where. */
export const EVIDENCE_UPLOAD_DETAIL =
  'Sent to the backend as unverified evidence attached to your report, once the report is stored.'

/** Error codes that a retry can plausibly fix - a transport failure, or a
 *  storage failure the backend itself asks the client to retry later. */
const RETRYABLE_CODES = new Set([
  'network_error',
  'media_unavailable',
  'media_not_durable',
])

/**
 * Whether offering "Try again" is honest.
 *
 * A rejected photo, a bad reading, or a conflicting payload will be rejected
 * identically on a second attempt, so the UI says what to change instead of
 * pretending another try might work. Only transport and storage failures are
 * treated as retryable.
 */
export function isRetryableEvidenceError(status: number, code: string): boolean {
  if (code === 'conflict') return false
  if (status === 0) return true
  return RETRYABLE_CODES.has(code) || status >= 500
}

/** The human reading of a rejection, kept close to the codes so the UI can
 *  explain one without inventing its own wording. */
export function explainEvidenceError(status: number, code: string): string {
  switch (code) {
    case 'network_error':
      return 'The upload never reached the server, or the connection dropped mid-transfer. ' +
        'Nothing is known about what arrived, so the same upload can simply be sent again.'
    case 'unsupported_media_type':
      return 'That file type is not accepted. Use a JPEG, PNG or WebP photo.'
    case 'unrecognized_media_content':
      return 'Those bytes are not a photo. A renamed file is not an image.'
    case 'media_content_invalid':
      return 'That photo arrived incomplete - the usual sign of an interrupted transfer. ' +
        'Pick the file again and retry.'
    case 'media_content_mismatch':
      return 'The photo does not match the format it was announced as.'
    case 'media_too_large':
      return 'That photo is larger than the server accepts.'
    case 'media_unavailable':
      return 'The server could not store the photo right now. Your report is safe - retry the upload.'
    case 'media_not_durable':
      return 'The server stored the photo but could not read it back, so it did not record it. Retry.'
    case 'validation_error':
      return 'The backend rejected a field. Check the reading and its time.'
    case 'conflict':
      return 'This report already has different evidence stored under the same key. ' +
        'The stored record was left untouched.'
    case 'not_found':
      return 'The server does not know this report, so the evidence has nowhere to go.'
    default:
      return status >= 500
        ? 'The server could not store the evidence. The report itself is already safe - retry.'
        : 'The backend refused the evidence.'
  }
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/** What the confirmation lists for a stored sensor reading. */
export function sensorSummary(evidence: ReportEvidenceOut): string | null {
  const sensor = evidence.sensor
  if (sensor === null) return null
  const label = SENSOR_POLLUTANT_LABELS[sensor.pollutant as SensorPollutant] ?? sensor.pollutant
  const value = Number.isInteger(sensor.value) ? String(sensor.value) : String(sensor.value)
  return `${label} ${value} ${sensor.unit} at ${new Date(sensor.measured_at).toLocaleString()}`
}
