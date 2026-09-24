import { formatCount, formatPercent, relativeTime, STALE_SOURCE_HOURS } from '../lib/format'
import { coverageSentence, missingInputs, sourceAgeNote, warningLines } from '../lib/runQuality'
import { SidePanel } from './SidePanel'
import { useMapUi } from '../state/MapUiContext'
import type { RunFacts, Staleness } from '../lib/runFacts'
import type { AsyncResource } from '../hooks/useApiResource'
import type { GridStateOut } from '../lib/types'

/** How the run's mode reads out loud. "Demo" is the one that carries a
 *  warning: it means illustrative, not measured. */
function modeLabel(facts: RunFacts): { text: string; className: string } {
  switch (facts.mode) {
    case 'demo':
      return { text: 'Demo · illustrative, not measured', className: 'run-mode run-mode-demo' }
    case 'mixed':
      return { text: 'Mixed observed and modeled inputs', className: 'run-mode run-mode-mixed' }
    case 'live':
      return { text: 'Live', className: 'run-mode run-mode-live' }
    default:
      return { text: 'Mode not reported', className: 'run-mode' }
  }
}

/**
 * The published run this dashboard is showing, stated in one place.
 *
 * Everything here is a field the backend sent with the same response the map
 * is drawn from — run time, mode, source age, coverage and prediction method
 * are all read off that one envelope and its cells, so this panel can never
 * describe a different run than the map is painting. Ages are given as a
 * number *and* a plain reading ("31h ago"), because a badge that only says
 * "stale" hides the fact a reader needs to judge it.
 */
export function RunStatusPanel({
  resource,
  facts,
  staleness,
}: {
  resource: AsyncResource<GridStateOut[]>
  facts: RunFacts
  staleness: Staleness
}) {
  const { state, dispatch } = useMapUi()
  const loading = resource.status === 'loading' || resource.status === 'idle'
  const mode = modeLabel(facts)
  const published = relativeTime(facts.generatedAt)
  const missing = missingInputs(facts)
  const warnings = warningLines(facts)

  return (
    <SidePanel
      id="run-panel"
      side="right"
      open={state.runPanelOpen}
      onToggle={() => dispatch({ type: 'TOGGLE_RUN_PANEL' })}
      label={state.runPanelOpen ? 'Hide run status' : 'Show run status'}
    >
      <div className="panel run-status">
        <section>
          <h3>Published run</h3>
          {loading ? (
            <p className="muted">Loading the current run…</p>
          ) : (
            <>
              <div className="run-mode-row">
                <span className={mode.className}>{mode.text}</span>
                {staleness.stale && (
                  <span className="run-stale-badge" title={staleness.reasons.join(' ')}>
                    Stale
                  </span>
                )}
              </div>

              <dl className="run-status-grid">
                <dt>Run time</dt>
                <dd>
                  {facts.generatedAt === undefined ? (
                    '—'
                  ) : (
                    <>
                      {new Date(facts.generatedAt).toLocaleString()}
                      {published !== null && <span className="muted"> · {published}</span>}
                    </>
                  )}
                </dd>

                <dt>Run id</dt>
                <dd className="run-id">{facts.runId ?? '—'}</dd>

                <dt title={`Age of the newest observation feeding this run. Above ${STALE_SOURCE_HOURS}h the run is flagged stale; the backend reports none for runs built without live observations.`}>
                  Source age
                </dt>
                <dd>
                  {facts.sourceAgeHours === null
                    ? 'Not reported'
                    : `${facts.sourceAgeHours.toFixed(1)}h`}
                  {sourceAgeNote(facts) !== '' && (
                    <span className="muted run-fact-note"> · {sourceAgeNote(facts)}</span>
                  )}
                </dd>

                <dt title="How much of the requested view this run actually covers.">Coverage</dt>
                <dd>
                  {facts.coverage === null ? (
                    'Not reported'
                  ) : (
                    <>
                      {formatPercent(facts.coverage.covered_fraction)}
                      <span className="muted">
                        {' '}
                        · {formatCount(facts.coverage.returned_cells)}/
                        {formatCount(facts.coverage.requested_cells)} cells at res{' '}
                        {facts.coverage.resolution}
                        {facts.coverage.unsupported_cells > 0 && (
                          <> · {formatCount(facts.coverage.unsupported_cells)} outside the run</>
                        )}
                      </span>
                    </>
                  )}
                </dd>

                <dt title="How the values in this run were produced, as the backend recorded it.">
                  Prediction method
                </dt>
                <dd>
                  {facts.predictionMethods.length === 0
                    ? '—'
                    : facts.predictionMethods.join(', ')}
                  {facts.modelVersion !== null && (
                    <span className="muted"> · model {facts.modelVersion}</span>
                  )}
                </dd>

                {facts.inputKind !== null && (
                  <>
                    <dt>Inputs</dt>
                    <dd>
                      {facts.inputKind}
                      {facts.featureSchemaVersion !== null && (
                        <span className="muted"> · {facts.featureSchemaVersion}</span>
                      )}
                    </dd>
                  </>
                )}

                <dt title="Cells in this read that carry a population-weighted concentration, out of the cells returned.">
                  Population
                </dt>
                <dd>
                  {facts.population.available ? (
                    <>
                      {formatCount(facts.population.cellsWithExposure)}/
                      {formatCount(facts.population.cellsTotal)} cells
                      <span className="muted">
                        {' '}
                        · {formatCount(facts.population.coveredPopulation)} residents covered
                        {facts.population.datasetVersion !== null &&
                          ` · ${facts.population.datasetVersion}`}
                      </span>
                    </>
                  ) : (
                    <span className="muted">No population estimate in this run</span>
                  )}
                </dd>
              </dl>

              {facts.attribution !== undefined && facts.attribution.length > 0 && (
                <p className="muted run-attribution">
                  {facts.attribution.map((ref) => ref.attribution || ref.dataset_id).join(' · ')}
                </p>
              )}

              {coverageSentence(facts) !== null && (
                <p className="muted run-coverage-note">{coverageSentence(facts)}</p>
              )}

              {missing.length > 0 && (
                <div className="run-inputs" role="note">
                  <h4>Missing inputs</h4>
                  <p className="muted">
                    This run has no value for the following, so nothing was substituted for them:
                  </p>
                  <ul className="run-input-list">
                    {missing.map((item) => (
                      <li key={item.field} title={item.field}>
                        {item.label}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {warnings.length > 0 && (
                <div className="run-warnings" role="note">
                  <h4>Backend warnings</h4>
                  <ul className="run-warning-list">
                    {warnings.map((warning) => (
                      <li key={warning}>{warning}</li>
                    ))}
                  </ul>
                </div>
              )}

              {staleness.stale && (
                <p className="run-stale-detail" role="status">
                  {staleness.reasons.join(' ')} Values may no longer describe current conditions.
                </p>
              )}
            </>
          )}
        </section>
      </div>
    </SidePanel>
  )
}
