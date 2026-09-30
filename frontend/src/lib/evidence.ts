// Uploading a photo to a citizen report (F2).
//
// This is the only place in the web client that touches a file, and it is
// deliberately separate from the JSON API helpers: a multipart upload cannot go
// through `apiPost`, because it needs the body to stay a live `FormData` for
// `XMLHttpRequest` to stream, and because it needs progress events that `fetch`
// does not expose.
//
// **The report is never blocked on the photo.** `attachPhoto` is called after
// the report exists and its failure is swallowed by the caller, because F2
// requires a report to succeed without one. There is no code path here that
// can turn a rejected photo into a lost report.

import { API_BASE_URL } from './api'
import type { Envelope } from './types'

/** What the backend records about an upload. Mirrors `EvidenceOut`.
 *
 *  There is no storage key, no derivative key and no filename in this shape,
 *  and that is the point: the client is told an id and a state, and the bytes
 *  come from the reviewer-gated route rather than from a link we hold. */
export interface EvidenceOut {
  id: number
  review_state: string
  scan_state: string
  width: number | null
  height: number | null
  byte_count: number
}

export type GeminiPossibleEventType =
  | 'smoke'
  | 'fire'
  | 'industrial plume'
  | 'other'
  | 'unclear'

export interface GeminiVisualAssessment {
  visible_observations: string[]
  possible_event_type: GeminiPossibleEventType
  visual_support: string[]
  missing_information: string[]
  uncertainty: number
  reviewer_summary: string
}

export interface EvidenceAssessmentOut {
  id: number
  report_id: number
  evidence_id: number
  assessment: GeminiVisualAssessment
  model_id: string
  prompt_version: string
  schema_version: string
  consented_at: string
  generated_at: string
}

export class EvidenceError extends Error {
  readonly code: string
  readonly status: number

  constructor(message: string, code: string, status: number) {
    super(message)
    this.name = 'EvidenceError'
    this.code = code
    this.status = status
  }
}

/** True when the deployment has photo capture turned off, as opposed to the
 *  upload having been refused for some other reason. The two need different
 *  messages: one is "not here", the other is "try again". */
export function isMediaUnavailable(error: unknown): boolean {
  return error instanceof EvidenceError && error.code === 'media_unavailable'
}

export interface UploadOptions {
  reportId: number
  file: File
  consent: boolean
  onProgress?: (fraction: number) => void
  signal?: AbortSignal
}

/** POST one photo against an existing report.
 *
 *  `onProgress` reports 0..1 from the browser's own upload progress, which is
 *  the only honest source available: the server cannot tell a stalled upload
 *  from a slow one, so a progress bar driven by anything else would be theatre.
 */
export function attachPhoto({
  reportId,
  file,
  consent,
  onProgress,
  signal,
}: UploadOptions): Promise<Envelope<EvidenceOut>> {
  return new Promise((resolve, reject) => {
    const form = new FormData()
    form.append('photo', file, file.name)
    form.append('consent', consent ? 'true' : 'false')

    const request = new XMLHttpRequest()
    request.open('POST', `${API_BASE_URL}/reports/${reportId}/evidence`)
    request.responseType = 'json'

    if (onProgress && request.upload) {
      request.upload.onprogress = (event) => {
        if (event.lengthComputable && event.total > 0) {
          onProgress(Math.min(1, event.loaded / event.total))
        }
      }
    }

    request.onload = () => {
      const body = request.response
      if (request.status >= 200 && request.status < 300) {
        onProgress?.(1)
        resolve(body as Envelope<EvidenceOut>)
        return
      }
      const error = body?.error
      reject(
        new EvidenceError(
          error?.message ?? `The photo was refused (HTTP ${request.status}).`,
          error?.code ?? 'upload_failed',
          request.status,
        ),
      )
    }
    // A network drop and an abort land here. The message is deliberately
    // retry-shaped: from the citizen's side this is a flaky connection, not a
    // broken form, and the fix is to press the button again.
    request.onerror = () =>
      reject(
        new EvidenceError(
          'The photo did not finish uploading. Check your connection and try again.',
          'network_error',
          0,
        ),
      )
    request.onabort = () =>
      reject(new EvidenceError('Upload cancelled.', 'aborted', 0))

    signal?.addEventListener('abort', () => request.abort(), { once: true })
    request.send(form)
  })
}

/** The photos already attached to a report. States only - never bytes. */
export async function fetchEvidence(reportId: number): Promise<EvidenceOut[]> {
  const response = await fetch(`${API_BASE_URL}/reports/${reportId}/evidence`)
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new EvidenceError(
      body?.error?.message ?? `Could not load the photos (HTTP ${response.status}).`,
      body?.error?.code ?? 'list_failed',
      response.status,
    )
  }
  const payload = (await response.json()) as Envelope<EvidenceOut[]>
  return payload.data
}

/** Fetch the metadata-free reviewer image. The shared reviewer key is entered
 *  at runtime and never persisted by this client. */
export async function fetchEvidenceDerivative(
  reportId: number,
  evidenceId: number,
  reviewerKey: string,
  signal?: AbortSignal,
): Promise<Blob> {
  const response = await fetch(
    `${API_BASE_URL}/reports/${reportId}/evidence/${evidenceId}/derivative`,
    {
      headers: { 'X-Reviewer-Key': reviewerKey },
      cache: 'no-store',
      signal,
    },
  )
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new EvidenceError(
      body?.error?.message ?? `Could not load the photo (HTTP ${response.status}).`,
      body?.error?.code ?? 'preview_failed',
      response.status,
    )
  }
  return response.blob()
}

/** Record a reviewer decision about the photo only, never the report itself. */
export async function setEvidenceReviewState(
  reportId: number,
  evidenceId: number,
  reviewerKey: string,
  reviewState: 'approved' | 'rejected',
): Promise<EvidenceOut> {
  const response = await fetch(
    `${API_BASE_URL}/reports/${reportId}/evidence/${evidenceId}/review`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Reviewer-Key': reviewerKey,
      },
      body: JSON.stringify({ review_state: reviewState }),
    },
  )
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new EvidenceError(
      body?.error?.message ?? `Could not review the photo (HTTP ${response.status}).`,
      body?.error?.code ?? 'review_failed',
      response.status,
    )
  }
  const payload = (await response.json()) as Envelope<EvidenceOut>
  return payload.data
}

/** Fetch a saved Gemini photo assessment if one exists for this evidence.
 *  Returns null if no assessment has been run yet (HTTP 404). */
export async function fetchEvidenceAssessment(
  reportId: number,
  evidenceId: number,
  reviewerKey: string,
  signal?: AbortSignal,
): Promise<EvidenceAssessmentOut | null> {
  const response = await fetch(
    `${API_BASE_URL}/reports/${reportId}/evidence/${evidenceId}/analysis`,
    {
      headers: {
        'X-Reviewer-Key': reviewerKey,
      },
      cache: 'no-store',
      signal,
    },
  )
  if (response.status === 404) {
    return null
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new EvidenceError(
      body?.error?.message ?? `Could not load AI assessment (HTTP ${response.status}).`,
      body?.error?.code ?? 'gemini_fetch_failed',
      response.status,
    )
  }
  const payload = (await response.json()) as Envelope<EvidenceAssessmentOut>
  return payload.data
}

export interface AnalyzeOptions {
  consent: boolean
  reanalyze?: boolean
  signal?: AbortSignal
}

/** Request Gemini multimodal visual assessment of the sanitized photo derivative.
 *  Advisory only; never changes evidence or report review state. */
export async function analyzeEvidenceWithGemini(
  reportId: number,
  evidenceId: number,
  reviewerKey: string,
  options: AnalyzeOptions,
): Promise<EvidenceAssessmentOut> {
  const response = await fetch(
    `${API_BASE_URL}/reports/${reportId}/evidence/${evidenceId}/analysis`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Reviewer-Key': reviewerKey,
      },
      body: JSON.stringify({
        consent: options.consent,
        reanalyze: options.reanalyze ?? false,
      }),
      signal: options.signal,
    },
  )
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new EvidenceError(
      body?.error?.message ?? `Gemini analysis could not be completed (HTTP ${response.status}).`,
      body?.error?.code ?? 'gemini_analysis_failed',
      response.status,
    )
  }
  const payload = (await response.json()) as Envelope<EvidenceAssessmentOut>
  return payload.data
}

/** Client-side size gate, so an oversized file is refused before it is sent.
 *
 *  Only a convenience: the server enforces the same limit on the bytes it
 *  actually receives, and a client that lies to itself about the size gains
 *  nothing because the server does not believe it either. Mirrored here purely
 *  to save the user a pointless upload.
 */
// Leave headroom below Vercel's 4.5 MB whole-request cap for multipart framing.
export const MAX_PHOTO_BYTES = 4 * 1024 * 1024

export function describePhotoProblem(file: File): string | null {
  if (file.size === 0) return 'That file is empty.'
  if (file.size > MAX_PHOTO_BYTES) {
    const mb = Math.round(MAX_PHOTO_BYTES / (1024 * 1024))
    return `That photo is ${(file.size / (1024 * 1024)).toFixed(1)} MB. The limit is ${mb} MB.`
  }
  // A hint only. The server decides from the bytes, so a wrong extension or a
  // lying content-type changes nothing about whether the upload is accepted.
  if (!/^image\/(jpeg|png|webp)$/i.test(file.type) && file.type !== '') {
    return 'That looks like it may not be a JPEG, PNG or WebP. It will still be checked.'
  }
  return null
}

