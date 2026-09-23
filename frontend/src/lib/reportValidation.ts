// Client-side mirror of the documented POST /api/v1/reports bounds.
//
// The backend is the authority (app.api.schemas.FireReportIn: latitude
// [-90,90], longitude [-180,180], smoke_intensity 1..5, duration_hours 0..24,
// notes <= 280 chars, client_report_id 1..64 chars). This module exists so a
// mistyped report fails here, next to the field that caused it, instead of
// coming back as a 422 the user has to interpret.
//
// It deliberately does NOT re-implement anything the backend decides (snapping
// to an H3 cell, whether the report is active, its id). Validation is the only
// thing duplicated, and it is duplicated only to fail faster.

import type { FireReportKind, FireReportSubmit } from './types'

/** Matches FireReportIn.notes' max_length. */
export const MAX_NOTES = 280

/** The fields a user can get wrong, as the form names them. */
export type FireReportField =
  | 'latitude'
  | 'longitude'
  | 'smoke_intensity'
  | 'duration_hours'
  | 'notes'

export type FireReportFieldErrors = Partial<Record<FireReportField, string>>

/** Human labels, so the summary line and the inline errors agree. */
export const FIELD_LABELS: Record<FireReportField, string> = {
  latitude: 'Latitude',
  longitude: 'Longitude',
  smoke_intensity: 'Smoke amount',
  duration_hours: 'Duration',
  notes: 'Notes',
}

export interface FireReportDraft {
  latitude: number
  longitude: number
  kind: FireReportKind
  smoke_intensity: number
  duration_hours: number
  notes: string
}

/**
 * Check a draft against the documented bounds.
 *
 * Returns an empty object when the draft is sendable. Every bound is checked
 * (not just the first failure) so the form can show all of them at once rather
 * than one per attempt.
 */
export function validateFireReport(draft: FireReportDraft): FireReportFieldErrors {
  const errors: FireReportFieldErrors = {}

  if (!Number.isFinite(draft.latitude) || draft.latitude < -90 || draft.latitude > 90) {
    errors.latitude = 'Latitude must be between -90 and 90.'
  }
  if (!Number.isFinite(draft.longitude) || draft.longitude < -180 || draft.longitude > 180) {
    errors.longitude = 'Longitude must be between -180 and 180.'
  }
  if (!Number.isInteger(draft.smoke_intensity) || draft.smoke_intensity < 1 || draft.smoke_intensity > 5) {
    errors.smoke_intensity = 'Smoke amount must be 1 (low) to 5 (extreme).'
  }
  if (!Number.isFinite(draft.duration_hours) || draft.duration_hours < 0 || draft.duration_hours > 24) {
    errors.duration_hours = 'Duration must be between 0 and 24 hours.'
  }
  if (draft.notes.length > MAX_NOTES) {
    errors.notes = `Notes must be at most ${MAX_NOTES} characters (currently ${draft.notes.length}).`
  }

  return errors
}

export function hasErrors(errors: FireReportFieldErrors): boolean {
  return Object.keys(errors).length > 0
}

/** "Notes must be at most 280 characters." — one line for the form summary. */
export function errorSummary(errors: FireReportFieldErrors): string {
  const fields = Object.keys(errors) as FireReportField[]
  if (fields.length === 0) return ''
  const names = fields.map((field) => FIELD_LABELS[field]).join(', ')
  return `Please fix: ${names}.`
}

/** Build the request body from a validated draft. Omitted optional fields are
 *  left out entirely, matching what the backend treats as absent. */
export function toSubmitBody(
  draft: FireReportDraft,
  clientReportId: string,
): FireReportSubmit {
  const notes = draft.notes.trim()
  return {
    latitude: draft.latitude,
    longitude: draft.longitude,
    kind: draft.kind,
    smoke_intensity: draft.smoke_intensity,
    duration_hours: draft.duration_hours,
    ...(notes === '' ? {} : { notes }),
    client_report_id: clientReportId,
  }
}
