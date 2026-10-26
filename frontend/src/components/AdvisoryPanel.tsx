import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { ApiError, generateAdvisory } from '../lib/api'
import type { AdvisoryRecordOut } from '../lib/types'

function displayTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? 'unreadable time'
    : `${date.toLocaleString('en-GB', { timeZone: 'UTC' })} UTC`
}

function exportText(record: AdvisoryRecordOut, narrative: string): string {
  const { bundle } = record
  const { context } = bundle
  const edited = narrative.trim() !== bundle.summary
  return [
    bundle.draft_label,
    'DRAFT FOR HUMAN REVIEW — not an official notice, confirmed event, or GRAP stage.',
    `Generation status: ${providerLabel(record)}`,
    `Model: ${bundle.model_id ?? 'not used'}`,
    `Prompt/schema: ${bundle.prompt_version ?? 'none'} / ${bundle.template_version}`,
    `Data mode: ${context.mode}${context.synthetic ? ' · synthetic inputs' : ''}${context.fallback ? ' · fallback inputs' : ''}${context.stale ? ' · stale inputs' : ''}`,
    `Published alert: ${context.source_id}`,
    `Prediction run: ${context.run_id}`,
    `Brief generated: ${displayTime(record.generated_at)}`,
    `Forecast valid: ${displayTime(context.forecast_at)} · +${context.forecast_hours} h`,
    `Forecast PM2.5: ${context.forecast_pm25.toFixed(1)} µg/m³`,
    '',
    edited ? 'Locally edited narrative (not saved to the server):' : 'Narrative:',
    narrative.trim(),
    ...(edited ? ['', 'Generated original:', bundle.summary] : []),
    '',
    'Published facts:',
    ...context.facts.map((fact) => `- ${fact.label}: ${fact.value} [${fact.evidence_id}]`),
    '',
    'Missing evidence:',
    ...(context.missing_evidence.length > 0
      ? context.missing_evidence.map((item) => `- ${item}`)
      : ['- None recorded']),
    '',
    'Inspection checks:',
    ...bundle.inspection_checklist.map((item) => `- ${item}`),
    '',
    'Source references:',
    ...(context.sources.length > 0
      ? context.sources.map(
          (source) => `- ${source.source}: ${source.description} [${source.evidence_id}]`,
        )
      : ['- No source references were recorded']),
    '',
    'Generated-text evidence references:',
    ...(bundle.generated_evidence_ids.length > 0
      ? bundle.generated_evidence_ids.map((id) => `- ${id}`)
      : ['- None; deterministic template']),
  ].join('\n')
}

async function copyText(value: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(value)
      return
    } catch {
      // Try the user-gesture-compatible legacy path below.
    }
  }

  const textarea = document.createElement('textarea')
  textarea.value = value
  textarea.setAttribute('readonly', '')
  textarea.style.position = 'fixed'
  textarea.style.opacity = '0'
  document.body.appendChild(textarea)
  try {
    textarea.select()
    if (!document.execCommand('copy')) throw new Error('Clipboard access was denied.')
  } finally {
    textarea.remove()
  }
}

function providerLabel(record: AdvisoryRecordOut): string {
  if (record.bundle.provider_status === 'gemini') return 'Gemini draft · review before use'
  if (record.bundle.provider_status === 'template_fallback') {
    return 'Template fallback · Gemini text was not used'
  }
  return 'Deterministic template · Gemini was not contacted'
}

export function AdvisoryPanel({
  sourceId,
  panelId,
  onClose,
}: {
  sourceId: string
  panelId: string
  onClose: () => void
}) {
  const [reviewerKey, setReviewerKey] = useState('')
  const [consent, setConsent] = useState(false)
  const [busy, setBusy] = useState(false)
  const [record, setRecord] = useState<AdvisoryRecordOut | null>(null)
  const [narrative, setNarrative] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const controller = useRef<AbortController | null>(null)
  const printRoot = useRef<HTMLElement | null>(null)

  useEffect(() => () => controller.current?.abort(), [])

  const generate = async () => {
    if (busy || !reviewerKey.trim()) return
    controller.current?.abort()
    const active = new AbortController()
    controller.current = active
    setBusy(true)
    setError(null)
    setNotice(null)
    setRecord(null)
    try {
      const result = await generateAdvisory(sourceId, reviewerKey.trim(), consent, active.signal)
      setRecord(result.data)
      setNarrative(result.data.bundle.summary)
    } catch (cause) {
      if (active.signal.aborted) return
      setError(
        cause instanceof ApiError
          ? cause.message
          : cause instanceof Error
            ? cause.message
            : 'Could not generate the advisory draft.',
      )
    } finally {
      if (!active.signal.aborted) setBusy(false)
      if (controller.current === active) controller.current = null
    }
  }

  const copy = async () => {
    if (record === null) return
    try {
      await copyText(exportText(record, narrative))
      setNotice('Full draft copied with its data mode, timestamps, and evidence references.')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not copy the draft.')
    }
  }

  const print = () => {
    document.querySelectorAll('.advisory-print-root').forEach((node) => {
      node.removeAttribute('data-print-active')
    })
    const target = printRoot.current
    if (target === null) return
    target.setAttribute('data-print-active', 'true')
    const cleanup = () => {
      target.removeAttribute('data-print-active')
      window.removeEventListener('afterprint', cleanup)
    }
    window.addEventListener('afterprint', cleanup)
    window.print()
  }

  const bundle = record?.bundle
  const context = bundle?.context
  const locallyEdited = bundle !== undefined && narrative.trim() !== bundle.summary

  return (
    <>
      <section
        id={panelId}
        className="advisory-panel"
        aria-labelledby={`${panelId}-title`}
        aria-busy={busy}
      >
        <div className="advisory-heading-row">
          <div>
            <h3 id={`${panelId}-title`}>Draft inspection brief</h3>
            <p className="advisory-note">
              Reviewer-only working text. It does not assign an official stage or send an order.
            </p>
          </div>
          <button
            type="button"
            className="advisory-close advisory-no-print"
            onClick={onClose}
            aria-label="Close draft inspection brief"
          >
            Close
          </button>
        </div>

        <label className="advisory-key advisory-no-print">
          Reviewer key
          <input
            type="password"
            autoComplete="off"
            disabled={busy}
            value={reviewerKey}
            onChange={(event) => {
              setReviewerKey(event.target.value)
              setError(null)
            }}
            placeholder="Enter the configured reviewer key"
          />
        </label>
        <p className="advisory-note advisory-no-print">
          The key stays in this open panel's memory and is sent only to the configured backend.
        </p>

        <label className="advisory-consent advisory-no-print">
          <input
            type="checkbox"
            checked={consent}
            disabled={busy}
            onChange={(event) => {
              setConsent(event.target.checked)
              setError(null)
            }}
          />
          <span>
            I consent to sending bounded published forecast and source facts to Gemini to draft
            text. Citizen reports, photos, identities, and free-form notes are not sent. Leave
            unchecked to request a deterministic template.
          </span>
        </label>

        {record === null && (
          <div className="advisory-actions advisory-no-print">
            <button
              type="button"
              className="alert-cta"
              onClick={() => void generate()}
              disabled={busy || !reviewerKey.trim()}
            >
              {busy ? 'Generating draft…' : 'Generate draft brief'}
            </button>
            {busy && (
              <span className="advisory-note" role="status">
                Using the saved template if a provider is unavailable.
              </span>
            )}
          </div>
        )}

        {error !== null && (
          <p className="advisory-error" role="alert">
            {error}
          </p>
        )}
        {notice !== null && (
          <p className="advisory-note" role="status">
            {notice}
          </p>
        )}

        {record !== null && bundle !== undefined && context !== undefined && (
          <div className="advisory-result" aria-live="polite">
            <div className="advisory-status-row">
              <strong>{bundle.draft_label}</strong>
              <span>{providerLabel(record)}</span>
            </div>
            {bundle.provider_message !== null && (
              <p className="advisory-note">{bundle.provider_message}</p>
            )}
            <dl className="advisory-metadata">
              <div>
                <dt>Data mode</dt>
                <dd>
                  {context.mode}
                  {context.synthetic ? ' · synthetic' : ''}
                  {context.fallback ? ' · fallback' : ''}
                  {context.stale ? ' · stale' : ''}
                </dd>
              </div>
              <div>
                <dt>Prediction run</dt>
                <dd>{context.run_id}</dd>
              </div>
              <div>
                <dt>Brief generated</dt>
                <dd>{displayTime(record.generated_at)}</dd>
              </div>
              <div>
                <dt>Forecast valid</dt>
                <dd>{displayTime(context.forecast_at)}</dd>
              </div>
            </dl>

            <label className="advisory-narrative-label" htmlFor="advisory-narrative">
              {locallyEdited ? 'Edited narrative · local only' : 'Narrative'}
            </label>
            <textarea
              id="advisory-narrative"
              className="advisory-narrative advisory-no-print"
              value={narrative}
              maxLength={1600}
              onChange={(event) => {
                setNarrative(event.target.value)
                setNotice(null)
              }}
              rows={4}
            />
            {locallyEdited && (
              <details className="advisory-original advisory-no-print">
                <summary>View generated original</summary>
                <p>{bundle.summary}</p>
                <span className="advisory-note">Local edits are not saved to the server.</span>
              </details>
            )}

            <h4>Published facts</h4>
            <ul className="advisory-evidence-list">
              {context.facts.map((fact, index) => (
                <li key={`${fact.evidence_id}-${index}`}>
                  <strong>{fact.label}:</strong> {fact.value} <code>{fact.evidence_id}</code>
                </li>
              ))}
            </ul>
            <h4>Missing evidence</h4>
            {context.missing_evidence.length > 0 ? (
              <ul className="advisory-evidence-list">
                {context.missing_evidence.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            ) : (
              <p className="advisory-note">No missing evidence was recorded.</p>
            )}
            <h4>Inspection checks</h4>
            <ul className="advisory-evidence-list">
              {bundle.inspection_checklist.map((item, index) => (
                <li key={`${item}-${index}`}>{item}</li>
              ))}
            </ul>
            <h4>Source references</h4>
            {context.sources.length > 0 ? (
              <ul className="advisory-evidence-list">
                {context.sources.map((source) => (
                  <li key={source.evidence_id}>
                    <strong>{source.source}:</strong> {source.description}{' '}
                    <code>{source.evidence_id}</code>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="advisory-note">No source references were recorded for this run.</p>
            )}
            <p className="advisory-note">
              Generated text references:{' '}
              {bundle.generated_evidence_ids.length > 0
                ? bundle.generated_evidence_ids.join(', ')
                : 'none; deterministic template'}
            </p>
            <div className="advisory-actions advisory-no-print">
              <button type="button" className="alert-cta" onClick={() => void copy()}>
                Copy full draft
              </button>
              <button type="button" className="alert-cta" onClick={print}>
                Print / Save as PDF
              </button>
            </div>
          </div>
        )}
      </section>
      {record !== null &&
        createPortal(
          <article
            ref={printRoot}
            className="advisory-print-root"
            aria-label="Printable advisory draft"
          >
            <pre>{exportText(record, narrative)}</pre>
          </article>,
          document.body,
        )}
    </>
  )
}
