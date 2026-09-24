import { missingPopulationMessage } from '../lib/runFacts'
import type { RunFacts, Staleness } from '../lib/runFacts'

/**
 * The two states the map has to be honest about but cannot draw: a run with no
 * population to weight by, and a run (or its observations) old enough that it
 * may no longer describe now.
 *
 * Both are rendered next to the map rather than swallowed into a colour: a
 * blank exposure layer with no explanation reads as "no pollution here", which
 * is the one thing it must never mean. Each notice names the run it is about,
 * so a reader can tell which publication the caveat applies to.
 *
 * The stale notice is not an error state — the map is still showing real,
 * attributed data — so it stays a status message, not an alert.
 */
export function DataQualityNotice({
  facts,
  staleness,
  metricIsExposure,
  hasData,
}: {
  facts: RunFacts
  staleness: Staleness
  metricIsExposure: boolean
  /** Whether the active layer has any cells to draw at all. */
  hasData: boolean
}) {
  const showMissingPopulation = metricIsExposure && hasData && !facts.population.available
  const showStale = staleness.stale && hasData

  if (!showMissingPopulation && !showStale) return null

  return (
    <>
      {showMissingPopulation && (
        <div className="banner banner-notice" role="status">
          <strong>No population to weight by.</strong> {missingPopulationMessage(facts)}
          {facts.population.unknownPopulation !== null &&
            facts.population.unknownPopulation > 0 && (
              <>
                {' '}
                {Math.round(facts.population.unknownPopulation).toLocaleString()} residents are in
                cells this frame has no prediction for.
              </>
            )}
        </div>
      )}

      {showStale && (
        <div className="banner banner-stale" role="status">
          <strong>Stale data.</strong> {staleness.reasons.join(' ')}
          {facts.runId !== undefined && (
            <span className="muted"> · run {facts.runId}</span>
          )}
        </div>
      )}
    </>
  )
}
