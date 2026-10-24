# Realtime Data Implementation Handoff

## Current state

- Continue on branch `beta`, tracking `origin/beta`.
- RT11 is complete and tested (`feat(cutover): validate live shadow collection and cutover readiness (RT11)`). All real-time milestones (RT01–RT11) are now delivered.
- The detailed scope and decisions remain in `docs/REAL_TIME_DATA_IMPLEMENTATION_PLAN.md`.
- Before editing, inspect `git status`. Preserve the existing untracked `docs/BUG_AUDIT.md` and `docs/REAL_TIME_DATA_IMPLEMENTATION_PLAN.md`; do not stage them as part of later steps.
- All real-time milestones have been verified and committed incrementally to `origin/beta`.

## RT11 delivered

RT11 delivers the live shadow collection measurement framework, operational failure scenario validation, and automated cutover readiness gatekeeper:

- **Shadow Collection Measurements:** Implemented `ShadowMeasurementService` measuring observation-to-provider lag, provider-to-app lag, active station coverage & freshness fraction, storage growth estimates, and disk headroom under active prediction retention.
- **Operational Failure Modes Exercised:** Implemented `FailureScenarioExerciser` methodically exercising all 10 critical operational failure modes:
  1. *Empty data:* Classifies upstream zero-row responses explicitly as `STATUS_EMPTY` in `SourceHealth` rather than masking outages as clean zero-pollution runs.
  2. *Stale data:* Observations exceeding maximum reading age are flagged as `STATUS_STALE` and excluded from spatial estimation.
  3. *Invalid units:* Non-standard pollutant concentration units (e.g. `ppm` for PM2.5) are rejected during validation.
  4. *Missing weather:* Weather ingestion gaps trigger decay-only dispersion with an explicit degraded-coverage note without failing the environmental run.
  5. *Clock skew:* Future-dated timestamps exceeding tolerance are rejected as clock skew.
  6. *Expired CDSE tokens:* Authentication and refresh failures degrade to `ScanVerdict.INSUFFICIENT_EVIDENCE` without crashing background workers.
  7. *HTTP 429 rate limits:* Raises `BudgetExhaustedError` and respects provider backoff headers without unbudgeted retry storms.
  8. *Multiple workers / distributed leasing:* PostgreSQL advisory leases serialize due jobs across competing workers, preventing duplicate runs.
  9. *Cloudy satellite swaths:* Cloud-obscured tiles evaluate to `INSUFFICIENT_EVIDENCE` without false-positive hotspot alerts.
  10. *Schema failures:* Corrupted or malformed payloads fail cleanly at validation boundaries without database corruption.
- **Cutover Readiness Gatekeeper:** Built `CutoverGate.evaluate(...)` verifying 7 comprehensive readiness gates before live traffic cutover:
  - *Mode isolation:* Strictly enforces 0% synthetic observations in `DataMode.LIVE`.
  - *Budget adherence:* Ensures provider usage remains within measured free allowances with adequate headroom.
  - *Station freshness:* Validates active station freshness fraction against regional minimums.
  - *Ingestion lag:* Checks observation-to-provider lag against real-time publication thresholds.
  - *Retention & storage:* Verifies active prediction retention compaction and media storage expiration sweeps.
  - *Failure resilience:* Validates graceful handling across all 10 operational failure scenarios.
  - *Rollback safety:* Retains dated last-good live data and verifies fallback/rollback procedures.
- **CLI Commands:** Added `shadow-run` and `cutover-gate` subcommands to `backend/app/cli.py` for pre-cutover auditing and automated deployment pipeline gating.

RT11 verification completed:
- New dedicated RT11 test suite: 17/17 passed (`backend/tests/test_rt11_shadow_and_cutover.py`).
- Combined RT10 + RT11 suites: 27/27 passed.
- Frontend production build (`npm run build`) passed cleanly.
- Ruff checks on all modified/new backend files passed with 0 errors.

Operational limits to preserve:
- Production deployments must execute `cutover-gate` before switching active traffic to live mode.
- Retain dated last-good live run data as fallback during upstream provider blackouts.
- Live runs must never contain synthetic or unverified contributions.

## Remaining work

All planned milestones (RT01–RT11) from the Real-Time Data Implementation Plan are complete and verified. Production cutover may proceed whenever live credentials and host infrastructure are provisioned.
