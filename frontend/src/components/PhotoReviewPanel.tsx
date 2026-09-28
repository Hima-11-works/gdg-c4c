import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchReportsWithStatus } from '../lib/api'
import {
  fetchEvidence,
  fetchEvidenceDerivative,
  setEvidenceReviewState,
  type EvidenceOut,
} from '../lib/evidence'
import { FIRE_KIND_LABELS } from '../lib/citizenReports'
import type { FireReportWithStatus } from '../lib/types'

interface ReviewItem {
  report: FireReportWithStatus
  evidence: EvidenceOut
}

interface PhotoPreview {
  evidenceId: number
  reviewerKey: string
  url?: string
  error?: string
}

export function PhotoReviewPanel({
  onClose,
  onReviewed,
}: {
  onClose: () => void
  onReviewed: () => void
}) {
  const [reviewerKey, setReviewerKey] = useState('')
  const [queue, setQueue] = useState<ReviewItem[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [queueError, setQueueError] = useState<string | null>(null)
  const [skippedCount, setSkippedCount] = useState(0)
  const [preview, setPreview] = useState<PhotoPreview | null>(null)
  const [decisionError, setDecisionError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [decisionInFlight, setDecisionInFlight] = useState(false)
  const loadGeneration = useRef(0)
  const panelRef = useRef<HTMLElement>(null)
  const onCloseRef = useRef(onClose)
  const decisionInFlightRef = useRef(decisionInFlight)
  const selected = queue.find((item) => item.evidence.id === selectedId) ?? queue[0] ?? null

  useEffect(() => {
    onCloseRef.current = onClose
    decisionInFlightRef.current = decisionInFlight
  }, [decisionInFlight, onClose])

  const loadQueue = useCallback(async () => {
    const generation = ++loadGeneration.current
    try {
      const reports = (await fetchReportsWithStatus()).data.filter(
        (report) => report.evidence_count > 0,
      )
      const items: ReviewItem[] = []
      let skipped = 0
      let next = 0
      const workers = Array.from({ length: Math.min(6, reports.length) }, async () => {
        while (next < reports.length) {
          const report = reports[next++]
          try {
            const evidence = await fetchEvidence(report.id)
            items.push(
              ...evidence
                .filter((row) => row.review_state === 'pending')
                .map((row) => ({ report, evidence: row })),
            )
          } catch {
            skipped += 1
          }
        }
      })
      await Promise.all(workers)
      items.sort((a, b) => b.report.reported_at.localeCompare(a.report.reported_at))
      if (loadGeneration.current !== generation) return
      setQueue(items)
      setSelectedId((previous) =>
        items.some((item) => item.evidence.id === previous)
          ? previous
          : (items[0]?.evidence.id ?? null),
      )
      setSkippedCount(skipped)
      setQueueError(null)
    } catch (error) {
      if (loadGeneration.current !== generation) return
      setQueueError(
        error instanceof Error ? error.message : 'Could not load the photo review queue.',
      )
    } finally {
      if (loadGeneration.current === generation) {
        setLoading(false)
        setRefreshing(false)
      }
    }
  }, [])

  const refreshQueue = () => {
    setRefreshing(true)
    setPreview(null)
    setQueueError(null)
    void loadQueue()
  }

  const selectedReportId = selected?.report.id
  const selectedEvidenceId = selected?.evidence.id
  const selectedScanState = selected?.evidence.scan_state
  const previewKey = reviewerKey.trim()
  const selectedPreview =
    preview !== null &&
    preview.evidenceId === selectedEvidenceId &&
    preview.reviewerKey === previewKey
      ? preview
      : null

  useEffect(() => {
    // This effect starts a remote read; loadQueue updates state only after its awaits settle.
    // oxlint-disable-next-line react/set-state-in-effect -- async queue request, not derived state.
    void loadQueue()
    return () => {
      loadGeneration.current += 1
    }
  }, [loadQueue])

  useEffect(() => {
    let active = true
    let objectUrl: string | null = null
    const controller = new AbortController()
    if (
      selectedReportId === undefined ||
      selectedEvidenceId === undefined ||
      selectedScanState !== 'clean' ||
      previewKey === ''
    )
      return

    void fetchEvidenceDerivative(
      selectedReportId,
      selectedEvidenceId,
      previewKey,
      controller.signal,
    )
      .then((blob) => {
        objectUrl = URL.createObjectURL(blob)
        if (active)
          setPreview({ evidenceId: selectedEvidenceId, reviewerKey: previewKey, url: objectUrl })
        else URL.revokeObjectURL(objectUrl)
      })
      .catch((error: unknown) => {
        if (!active || (error instanceof DOMException && error.name === 'AbortError')) return
        setPreview({
          evidenceId: selectedEvidenceId,
          reviewerKey: previewKey,
          error: error instanceof Error ? error.message : 'Could not load the photo preview.',
        })
      })

    return () => {
      active = false
      controller.abort()
      if (objectUrl !== null) URL.revokeObjectURL(objectUrl)
    }
  }, [selectedReportId, selectedEvidenceId, selectedScanState, previewKey])

  useEffect(() => {
    const panel = panelRef.current
    if (panel === null) return
    const previousFocus =
      document.activeElement instanceof HTMLElement ? document.activeElement : null
    const focusableSelector =
      'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
    const focusableItems = () =>
      Array.from(panel.querySelectorAll<HTMLElement>(focusableSelector)).filter(
        (item) => item.getClientRects().length > 0 && item.getAttribute('aria-hidden') !== 'true',
      )

    focusableItems()[0]?.focus()

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        if (!decisionInFlightRef.current) onCloseRef.current()
        return
      }
      if (event.key !== 'Tab') return

      const items = focusableItems()
      const first = items[0]
      const last = items.at(-1)
      if (first === undefined || last === undefined) {
        event.preventDefault()
        panel.focus()
        return
      }
      if (
        event.shiftKey &&
        (document.activeElement === first || !panel.contains(document.activeElement))
      ) {
        event.preventDefault()
        last.focus()
      } else if (
        !event.shiftKey &&
        (document.activeElement === last || !panel.contains(document.activeElement))
      ) {
        event.preventDefault()
        first.focus()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => {
      window.removeEventListener('keydown', onKeyDown)
      if (previousFocus?.isConnected) previousFocus.focus()
    }
  }, [])

  const decide = async (reviewState: 'approved' | 'rejected') => {
    if (
      selected === null ||
      previewKey === '' ||
      selectedPreview?.url === undefined ||
      decisionInFlight
    )
      return
    setDecisionInFlight(true)
    setDecisionError(null)
    setNotice(null)
    try {
      await setEvidenceReviewState(
        selected.report.id,
        selected.evidence.id,
        previewKey,
        reviewState,
      )
      const reviewed = selected
      setPreview(null)
      setQueue((current) => current.filter((item) => item.evidence.id !== reviewed.evidence.id))
      setSelectedId((current) => {
        if (current !== reviewed.evidence.id) return current
        return queue.find((item) => item.evidence.id !== reviewed.evidence.id)?.evidence.id ?? null
      })
      setNotice(
        `Photo evidence #${reviewed.evidence.id} ${reviewState}. The report status and air-quality model are unchanged.`,
      )
      onReviewed()
    } catch (error) {
      setDecisionError(
        error instanceof Error ? error.message : 'The photo review could not be saved.',
      )
    } finally {
      setDecisionInFlight(false)
    }
  }

  return (
    <div
      className="photo-review-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !decisionInFlight) onClose()
      }}
    >
      <section
        className="panel photo-review-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="photo-review-title"
        aria-describedby="photo-review-description"
        tabIndex={-1}
        ref={panelRef}
      >
        <header className="photo-review-header">
          <div>
            <h2 id="photo-review-title">Citizen photo review</h2>
            <p className="muted">Review photos attached to active citizen reports.</p>
          </div>
          <button
            type="button"
            className="report-form-close"
            onClick={onClose}
            disabled={decisionInFlight}
            aria-label="Close photo review"
          >
            ×
          </button>
        </header>

        <label className="photo-review-key">
          Reviewer key
          <input
            type="password"
            autoComplete="off"
            value={reviewerKey}
            onChange={(event) => {
              setPreview(null)
              setReviewerKey(event.target.value)
            }}
            placeholder="Enter the configured reviewer key"
          />
        </label>
        <p className="muted photo-review-note" id="photo-review-description">
          The key stays in this page's memory. Approving or rejecting a photo records a photo
          decision only; the report must be reviewed separately before it can affect the model.
        </p>

        <div className="photo-review-toolbar">
          <strong>
            {queue.length} pending photo{queue.length === 1 ? '' : 's'}
          </strong>
          <button
            type="button"
            className="panel photo-review-refresh"
            onClick={refreshQueue}
            disabled={refreshing}
          >
            {refreshing ? 'Refreshing…' : 'Refresh queue'}
          </button>
        </div>
        {queueError !== null && (
          <p className="report-form-error" role="alert">
            {queueError}
          </p>
        )}
        {skippedCount > 0 && (
          <p className="muted" role="status">
            Could not read photo metadata for {skippedCount} report{skippedCount === 1 ? '' : 's'}.
          </p>
        )}
        {notice !== null && (
          <p className="photo-review-notice" role="status">
            {notice}
          </p>
        )}

        <div className="photo-review-content">
          <nav className="photo-review-queue" aria-label="Pending citizen photos">
            {loading && queue.length === 0 && <p className="muted">Loading review queue…</p>}
            {!loading && queue.length === 0 && queueError === null && (
              <p className="muted">No pending citizen photos are available.</p>
            )}
            {queue.map(({ report, evidence }) => (
              <button
                key={evidence.id}
                type="button"
                className={`photo-review-item ${selected?.evidence.id === evidence.id ? 'is-selected' : ''}`}
                onClick={() => {
                  setPreview(null)
                  setSelectedId(evidence.id)
                  setDecisionError(null)
                }}
                aria-current={selected?.evidence.id === evidence.id ? 'true' : undefined}
              >
                <strong>
                  Report #{report.id} · Photo #{evidence.id}
                </strong>
                <span>
                  {FIRE_KIND_LABELS[report.kind]} ·{' '}
                  {new Date(report.reported_at).toLocaleDateString()}
                </span>
                <span className="muted">
                  {evidence.scan_state === 'clean' ? 'Ready to review' : 'Quarantined · no preview'}
                </span>
              </button>
            ))}
          </nav>

          <div className="photo-review-detail">
            {selected === null ? (
              <p className="muted">Select a photo to review its metadata and image.</p>
            ) : (
              <>
                <div className="photo-review-report">
                  <strong>Report #{selected.report.id}</strong>
                  <span>
                    {FIRE_KIND_LABELS[selected.report.kind]} · smoke{' '}
                    {selected.report.smoke_intensity}/5
                  </span>
                  <span>
                    {selected.report.latitude.toFixed(4)}, {selected.report.longitude.toFixed(4)} ·{' '}
                    {new Date(selected.report.reported_at).toLocaleString()}
                  </span>
                  {selected.report.notes && <p>{selected.report.notes}</p>}
                </div>
                {selected.evidence.scan_state === 'clean' ? (
                  <div className="photo-review-preview">
                    {selectedPreview?.url !== undefined ? (
                      <img
                        src={selectedPreview.url}
                        alt={`Photo evidence ${selected.evidence.id} for report ${selected.report.id}`}
                      />
                    ) : selectedPreview?.error !== undefined ? (
                      <p className="report-form-error" role="alert">
                        {selectedPreview.error}
                      </p>
                    ) : reviewerKey.trim() === '' ? (
                      <p className="muted">
                        Enter the reviewer key to load the protected photo preview.
                      </p>
                    ) : (
                      <p className="muted">Loading protected photo…</p>
                    )}
                  </div>
                ) : (
                  <div className="photo-review-quarantine" role="status">
                    This upload did not pass image validation, so the server will not show it as a
                    photo. Review the report separately; do not approve this unseen image.
                  </div>
                )}
                <p className="muted photo-review-metadata">
                  Photo #{selected.evidence.id} · {selected.evidence.width ?? 'Unknown'} ×{' '}
                  {selected.evidence.height ?? 'unknown'} ·{' '}
                  {(selected.evidence.byte_count / 1024).toFixed(0)} KB
                </p>
                {decisionError !== null && (
                  <p className="report-form-error" role="alert">
                    {decisionError}
                  </p>
                )}
                {selected.evidence.scan_state === 'clean' && (
                  <div className="photo-review-actions">
                    <button
                      type="button"
                      className="photo-review-approve"
                      disabled={
                        decisionInFlight || selectedPreview?.url === undefined || previewKey === ''
                      }
                      onClick={() => void decide('approved')}
                    >
                      {decisionInFlight ? 'Saving…' : 'Approve photo'}
                    </button>
                    <button
                      type="button"
                      className="photo-review-reject"
                      disabled={
                        decisionInFlight || selectedPreview?.url === undefined || previewKey === ''
                      }
                      onClick={() => void decide('rejected')}
                    >
                      {decisionInFlight ? 'Saving…' : 'Reject photo'}
                    </button>
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      </section>
    </div>
  )
}
