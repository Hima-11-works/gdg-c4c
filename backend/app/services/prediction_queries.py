"""Run-pinned v2 reads and native-resolution H3 aggregation."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

import h3

from app.core.config import get_settings
from app.domain.features import (
    FEATURE_SCHEMA_VERSION,
    DataMode,
    DatasetRef,
    FeatureQuality,
    InputKind,
)
from app.domain.h3_grid import cell_center
from app.domain.prediction import AlertCandidate, DEFAULT_REGION, PredictionResult, PredictionRun
from app.db.repositories.source_health import SourceHealth, SourceHealthRepository
from app.domain.repositories import PredictionPublicationRepository
from app.services import demo_data

DEFAULT_EXPOSURE_THRESHOLD_PM25 = 60.0

#: Marks a run this service synthesised rather than one that was published.
#: Used by `is_fallback`, by the id re-parse in `_demo_publication`, and by
#: the several places that must not ship a claim into the model, so the word
#: "fallback" has exactly one spelling in this file.
DEMO_FALLBACK_PREFIX = "demo-fallback-"

#: How old a run's data may be before it is called stale. 24h, matching the
#: web client's `STALE_RUN_HOURS`. It now lives here so the API and the client
#: cannot drift; the client's copy is a reimplementation of this.
STALE_RUN_SECONDS = 24 * 60 * 60

_DEMO_DATASET = DatasetRef(
  dataset_id="air-health-demo-v1",
  source="Air Health demo generator",
  product="illustrative regional pollution field",
  version="1",
  kind=InputKind.SYNTHETIC,
  region="india-demo",
  attribution="Air Health demo data",
  license="Project-generated synthetic data",
)


@dataclass(frozen=True, slots=True)
class ExposureSummary:
    population_weighted_pm25: float | None
    residents_above_threshold: float | None
    threshold_pm25: float
    covered_population: float
    unknown_population: float | None
    population_dataset_version: str | None
    scope: str


@dataclass(frozen=True, slots=True)
class PredictionCellView:
    h3_cell: str
    resolution: int
    horizon_hours: float
    valid_at: datetime
    latitude: float
    longitude: float
    pm25: float | None
    baseline_pm25: float | None
    lower_pm25: float | None
    upper_pm25: float | None
    pdi: float | None
    confidence: float
    spatial_coverage_fraction: float
    supported_native_cells: int
    expected_native_cells: int
    input_kind: InputKind
    prediction_method: str
    model_version: str | None
    feature_schema_version: str
    synthetic: bool
    quality: FeatureQuality
    dataset_refs: tuple[DatasetRef, ...]
    feature_vector: Mapping[str, float | None]
    exposure: ExposureSummary


class PredictionQueryService:
    def __init__(
    self,
    repository: PredictionPublicationRepository,
    *,
    native_resolution: int | None = None,
    region: str | None = None,
    source_health: SourceHealthRepository | None = None,
  ) -> None:
        self._repository = repository
        settings = get_settings()
        self.native_resolution = settings.h3_resolution if native_resolution is None else native_resolution
        self.region = region or DEFAULT_REGION
        self._demo_run: PredictionRun | None = None
        # F3: optional, because the read service is constructed in tests and in
        # the v1 paths without a health repository. Absent means "no health
        # recorded", which the envelope reports as an empty list - itself a
        # signal, rather than a reason to fail a read.
        self._health = source_health

    def source_health(self, run: PredictionRun | None = None) -> list[SourceHealth]:
        """Per-source health behind the data being served.

        Deliberately *not* run-pinned to `run`. An ingestion run and a
        prediction run are separate records with no foreign key between them, so
        correlating them by identity would be a fabrication. This returns the
        most recent ingestion run's health and is presented as a diagnostic -
        "was anything actually working when this data was produced" - rather
        than as provenance for the run itself.
        """
        if self._health is None:
            return []
        try:
            return self._health.list_latest_run()
        except Exception:
            # Health is diagnostic. A read must not fail because the diagnostic
            # table is unavailable.
            return []

    def run(self, run_id: str | None = None) -> PredictionRun:
        if run_id is not None:
            stored = self._repository.get_run(run_id)
            if stored is not None:
                return stored
            if run_id.startswith(DEMO_FALLBACK_PREFIX):
                demo_run = self._demo_publication(run_id=run_id)
                if demo_run is not None:
                    return demo_run[0]
            raise ValueError(f"published prediction run {run_id!r} was not found")
        stored = self._repository.latest_run(region=self.region)
        if stored is not None:
            return stored
        return self._demo_publication()[0]

    def is_fallback(self, run: PredictionRun) -> bool:
        """Whether `run` was synthesised rather than published.

        F3. The read path above fabricates a `demo-fallback-<hour>` run whenever
        no publication exists, and hands it back as an ordinary result. That is
        the right behaviour for a demo - an empty dashboard is worse than an
        honest synthetic one - and the wrong behaviour for anything that then
        *cannot tell*, because the run id rolls over on the hour and looks like
        a healthy pipeline. So the fact is recorded here and surfaced on the
        envelope as `is_fallback`, rather than left for a client to infer from
        the shape of a run id.

        The test is the id prefix, which is the same marker `_demo_publication`
        mints and the route re-parses, so there is one definition of
        "fallback" rather than two that can drift.
        """
        return run.run_id.startswith(DEMO_FALLBACK_PREFIX)

    def fallback_reason(self, run: PredictionRun) -> str | None:
        """One sentence saying why this is a fallback, or None for a real run."""
        if not self.is_fallback(run):
            return None
        return (
            "no prediction run has been published for this region, so this "
            "response is synthesised from stored grid rows; it is not a "
            "publication and no hourly pipeline produced it"
        )

    def age_seconds(self, run: PredictionRun, *, now: datetime | None = None) -> float:
        """Age of the run's data in seconds. Never negative.

        Clamped at zero rather than allowed to go negative on a clock skew: a
        negative age would render as a staleness bug when it is really a time
        problem, and the honest reading of "generated in the future" is "age
        unknown, so treat it as fresh", which is what clamping produces.
        """
        moment = now or datetime.now(UTC)
        return max(0.0, (moment - run.generated_at).total_seconds())

    def is_stale(
        self, run: PredictionRun, *, now: datetime | None = None
    ) -> bool:
        """Whether the data is older than the staleness budget.

        The rule lives in the service so the envelope, the CLI and any future
        scheduler all answer it identically. It used to exist only in the web
        client's `format.ts` (`STALE_RUN_HOURS = 24`), which meant the same
        question had two implementations and a client could disagree with the
        API about whether data was stale.
        """
        return self.age_seconds(run, now=now) > STALE_RUN_SECONDS

    def results(
        self,
        run: PredictionRun,
        *,
        cells: list[str] | None = None,
        horizons: set[float] | None = None,
    ) -> list[PredictionResult]:
        if run.run_id.startswith(DEMO_FALLBACK_PREFIX):
            return self._demo_results(run, cells or [], horizons)
        # Push both filters into SQL. A run holds one row per cell per horizon;
        # materializing the whole run and filtering here made even a small
        # detail read pay for every row and its three JSON columns.
        results = self._repository.list_results(
            run.run_id,
            horizons=sorted(horizons) if horizons is not None else None,
            cells=cells,
        )
        cell_set = None if cells is None else set(cells)
        return [
            row
            for row in results
            if (cell_set is None or row.h3_cell in cell_set)
            and (horizons is None or row.horizon_hours in horizons)
        ]

    def alert_candidates(
        self,
        run: PredictionRun,
        *,
        threshold_pm25: float,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[AlertCandidate]:
        """Fetch qualifying alerts, optionally as a page, without loading a run."""
        if run.run_id.startswith(DEMO_FALLBACK_PREFIX):
            rows = self.results(
                run, cells=self.target_cells(run, self.native_resolution)
            )
            current_by_cell = {
                row.h3_cell: row for row in rows if row.horizon_hours == 0
            }
            candidates = []
            for row in rows:
                if (
                    row.horizon_hours <= 0
                    or row.predicted_pm25 is None
                    or row.predicted_pm25 < threshold_pm25
                ):
                    continue
                current = current_by_cell.get(row.h3_cell)
                candidates.append(AlertCandidate(
                    h3_cell=row.h3_cell,
                    horizon_hours=row.horizon_hours,
                    valid_at=row.valid_at,
                    predicted_pm25=row.predicted_pm25,
                    current_pm25=current.predicted_pm25 if current else None,
                    confidence=row.quality.coverage_fraction,
                    synthetic=row.synthetic,
                ))
            candidates.sort(
                key=lambda item: (-item.predicted_pm25, item.horizon_hours, item.h3_cell)
            )
            return candidates[offset:] if limit is None else candidates[offset : offset + limit]
        return self._repository.list_alert_candidates(
            run.run_id,
            threshold_pm25=threshold_pm25,
            limit=limit,
            offset=offset,
        )

    def target_cells(self, run: PredictionRun, resolution: int) -> list[str]:
        if run.run_id.startswith(DEMO_FALLBACK_PREFIX):
            return sorted(state.h3_cell for state in demo_data.demo_grid_states(resolution))
        # Only the cell ids, so working out a resolution's coverage costs a
        # distinct-column scan rather than a full result load.
        cells = {
            cell
            if h3.get_resolution(cell) == resolution
            else h3.cell_to_parent(cell, resolution)
            for cell in self._repository.list_result_cells(run.run_id)
            if h3.get_resolution(cell) >= resolution
        }
        return sorted(cells)

    def source_cells_for_targets(
        self, run: PredictionRun, target_cells: list[str], *, resolution: int
    ) -> list[str]:
        """Return stored descendant cells covered by the requested H3 cells."""
        target_set = set(target_cells)
        return sorted(
            cell
            for cell in self._repository.list_result_cells(run.run_id)
            if h3.get_resolution(cell) >= resolution
            and (
                cell in target_set
                if h3.get_resolution(cell) == resolution
                else h3.cell_to_parent(cell, resolution) in target_set
            )
        )

    def aggregate(
        self,
        run: PredictionRun,
        *,
        target_cells: list[str],
        resolution: int,
        horizon: float,
        threshold_pm25: float = DEFAULT_EXPOSURE_THRESHOLD_PM25,
        source_cells: list[str] | None = None,
    ) -> list[PredictionCellView]:
        if resolution > self.native_resolution:
            raise ValueError(
                f"resolution {resolution} exceeds native prediction resolution "
                f"{self.native_resolution}; synthetic upscaling is not supported"
            )
        if not math.isfinite(threshold_pm25) or threshold_pm25 < 0:
            raise ValueError("threshold_pm25 must be a finite non-negative concentration")
        rows = self._rows_at_horizon(run, target_cells, horizon, source_cells)
        grouped: dict[str, list[PredictionResult]] = {cell: [] for cell in target_cells}
        for row in rows:
            source_resolution = h3.get_resolution(row.h3_cell)
            if source_resolution < resolution:
                continue
            parent = row.h3_cell if source_resolution == resolution else h3.cell_to_parent(row.h3_cell, resolution)
            if parent in grouped:
                grouped[parent].append(row)
        return [
            self._aggregate_cell(run, cell, resolution, horizon, grouped[cell], threshold_pm25)
            for cell in target_cells
        ]

    def detail(
        self,
        run: PredictionRun,
        h3_cell: str,
        *,
        resolution: int,
        threshold_pm25: float,
        source_cells: list[str] | None = None,
    ) -> PredictionCellView | None:
        if source_cells is None and not run.run_id.startswith(DEMO_FALLBACK_PREFIX):
            source_cells = self.source_cells_for_targets(
                run, [h3_cell], resolution=resolution
            )
        view = self.aggregate(
            run,
            target_cells=[h3_cell],
            resolution=resolution,
            horizon=0,
            threshold_pm25=threshold_pm25,
            source_cells=source_cells,
        )[0]
        if run.run_id.startswith(DEMO_FALLBACK_PREFIX):
            rows = self.results(run, cells=[h3_cell])
            available = any(row.horizon_hours >= 0 for row in rows)
        else:
            available = bool(source_cells)
        if view.supported_native_cells == 0 and not available:
            return None
        return view

    def horizons(self, run: PredictionRun) -> list[float]:
        if run.run_id.startswith(DEMO_FALLBACK_PREFIX):
            return [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        # A distinct list of ~25 numbers, not a load of every result row. This
        # is called by the meta route, by the forecast route's validation, and
        # by every aggregate; deriving it from a full result load made each of
        # those as expensive as the read it was about to do.
        return [h for h in self._repository.list_horizons(run.run_id) if h > 0]

    def _rows_at_horizon(
        self,
        run: PredictionRun,
        target_cells: list[str],
        horizon: float,
        source_cells: list[str] | None = None,
    ) -> list[PredictionResult]:
        if run.run_id.startswith(DEMO_FALLBACK_PREFIX):
            # The fallback is synthesised in memory, so there is nothing to
            # narrow and it filters as it always did.
            stored = self.results(
                run,
                cells=target_cells if source_cells is None else source_cells,
            )
        else:
            # Work out which horizons can answer this request *before*
            # materialising any rows, then load only those. A run publishes 25
            # horizons and a read needs one, or the two bracketing an
            # interpolation - so this is ~10k rows instead of the run's ~200k.
            #
            # The full anchor list, horizon 0 included: `horizons()` reports the
            # *forecast* horizons (0 is not one a client can ask to scrub to),
            # but 0 is a legitimate lower anchor to interpolate up from, and
            # using the filtered list made a +15m read find no lower anchor and
            # return nothing.
            anchors = self._repository.list_horizons(run.run_id)
            if horizon in anchors or horizon == 0:
                wanted = {horizon}
            else:
                lower = [anchor for anchor in anchors if anchor < horizon]
                upper = [anchor for anchor in anchors if anchor > horizon]
                if not lower or not upper:
                    return []
                wanted = {max(lower), min(upper)}
            stored = self.results(run, horizons=wanted, cells=source_cells)

        exact = [row for row in stored if row.horizon_hours == horizon]
        if exact or horizon == 0:
            return exact

        anchors = sorted({row.horizon_hours for row in stored})
        lower_options = [anchor for anchor in anchors if anchor < horizon]
        upper_options = [anchor for anchor in anchors if anchor > horizon]
        if not lower_options or not upper_options:
            return []
        lower_horizon = max(lower_options)
        upper_horizon = min(upper_options)
        ratio = (horizon - lower_horizon) / (upper_horizon - lower_horizon)
        by_cell: dict[tuple[str, float], PredictionResult] = {
            (row.h3_cell, row.horizon_hours): row for row in stored
        }
        interpolated = []
        target_resolution = h3.get_resolution(target_cells[0]) if target_cells else None
        target_set = set(target_cells)
        source_cells = sorted(
            cell
            for cell, anchor in by_cell
            if anchor == lower_horizon
            and target_resolution is not None
            and h3.get_resolution(cell) >= target_resolution
            and (
                cell in target_set
                if h3.get_resolution(cell) == target_resolution
                else h3.cell_to_parent(cell, target_resolution) in target_set
            )
        )
        for cell in source_cells:
            lower = by_cell.get((cell, lower_horizon))
            upper = by_cell.get((cell, upper_horizon))
            if lower is None or upper is None:
                continue
            predicted = _interpolate_number(lower.predicted_pm25, upper.predicted_pm25, ratio)
            baseline = _interpolate_number(lower.baseline_pm25, upper.baseline_pm25, ratio)
            if predicted is None:
                continue
            synthetic = lower.synthetic or upper.synthetic
            refs = _unique_refs([*lower.dataset_refs, *upper.dataset_refs])
            quality = FeatureQuality(
                coverage_fraction=min(
                    lower.quality.coverage_fraction, upper.quality.coverage_fraction
                ),
                observed_station_count=min(
                    lower.quality.observed_station_count,
                    upper.quality.observed_station_count,
                ),
                max_observation_age_hours=max(
                    (age for age in (
                        lower.quality.max_observation_age_hours,
                        upper.quality.max_observation_age_hours,
                    ) if age is not None),
                    default=None,
                ),
                missing_fields=tuple(sorted(set(lower.quality.missing_fields) | set(upper.quality.missing_fields))),
                warnings=tuple(sorted(
                    set(lower.quality.warnings)
                    | set(upper.quality.warnings)
                    | {"Interpolated between published forecast anchors; no calibrated interval."}
                )),
            )
            valid_at = lower.valid_at + (upper.valid_at - lower.valid_at) * ratio
            interpolated.append(
                PredictionResult(
                    run_id=run.run_id,
                    h3_cell=cell,
                    horizon_hours=horizon,
                    valid_at=valid_at,
                    baseline_pm25=baseline,
                    predicted_pm25=predicted,
                    lower_pm25=None,
                    upper_pm25=None,
                    prediction_method="interpolated-between-published-anchors",
                    model_version=(
                        lower.model_version
                        if lower.model_version == upper.model_version
                        else None
                    ),
                    feature_schema_version=run.feature_schema_version,
                    input_kind=InputKind.SYNTHETIC if synthetic else InputKind.MODELED,
                    synthetic=synthetic,
                    quality=quality,
                    dataset_refs=refs,
                    # Static fields such as population are unchanged within a run;
                    # dynamic fields remain those from the preceding issue anchor.
                    feature_vector=dict(lower.feature_vector),
                )
            )
        return interpolated

    def _demo_publication(
        self, *, run_id: str | None = None
    ) -> tuple[PredictionRun, list[PredictionResult]] | None:
        if run_id is None:
            hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
            canonical_run_id = f"{DEMO_FALLBACK_PREFIX}{hour:%Y%m%dT%H}Z"
        else:
            prefix = DEMO_FALLBACK_PREFIX
            try:
                hour = datetime.strptime(run_id.removeprefix(prefix), "%Y%m%dT%HZ").replace(
                    tzinfo=UTC
                )
            except ValueError:
                return None
            canonical_run_id = f"{DEMO_FALLBACK_PREFIX}{hour:%Y%m%dT%H}Z"
            if run_id != canonical_run_id:
                return None

        if self._demo_run is not None and self._demo_run.run_id == canonical_run_id:
            return self._demo_run, []

        demo_run = PredictionRun(
            run_id=canonical_run_id,
            generated_at=hour,
            published_at=hour,
            region=self.region,
            mode=DataMode.DEMO,
            feature_run_id="legacy-demo-field",
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            dataset_refs=(_DEMO_DATASET,),
            scenario_id="legacy-illustrative-field",
        )
        if run_id is None:
            self._demo_run = demo_run
        return demo_run, []

    def _demo_results(
        self, run: PredictionRun, cells: list[str], horizons: set[float] | None
    ) -> list[PredictionResult]:
        output: list[PredictionResult] = []
        requested = {0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0} if horizons is None else horizons
        for cell in cells:
            if not demo_data.is_within_demo_domain(cell):
                continue
            current = demo_data.generate_grid_state(cell, timestamp=run.generated_at)
            weather = demo_data.generate_weather_reading(cell, timestamp=run.generated_at)
            vector = {
                "current_pm25": current.pm25,
                "wind_speed_ms": weather.wind_speed,
                "wind_direction_deg": weather.wind_direction,
                "precipitation_mm": weather.precipitation,
                "boundary_layer_height_m": weather.boundary_layer_height,
                "temperature_c": weather.temperature,
                "relative_humidity_pct": weather.humidity,
            }
            quality = FeatureQuality(
                coverage_fraction=1.0,
                missing_fields=("population", "roads", "land_cover", "observed_station_count"),
                warnings=("Illustrative fallback data; environmental source inputs are not loaded.",),
            )
            for horizon in sorted(requested):
                if horizon == 0:
                    baseline = current.pm25
                    value = current.pm25
                    valid_at = run.generated_at
                    method = "synthetic-demo-field"
                else:
                    if horizon not in {1.0, 2.0, 3.0, 4.0, 5.0, 6.0}:
                        continue
                    forecast = demo_data.generate_forecast(cell, horizon, timestamp=run.generated_at)
                    baseline = current.pm25
                    value = forecast.predicted_pm25
                    valid_at = forecast.forecast_time
                    method = "synthetic-demo-forecast"
                output.append(
                    PredictionResult(
                        run_id=run.run_id,
                        h3_cell=cell,
                        horizon_hours=horizon,
                        valid_at=valid_at,
                        baseline_pm25=baseline,
                        predicted_pm25=value,
                        pdi=current.pdi if horizon == 0 else None,
                        prediction_method=method,
                        feature_schema_version=FEATURE_SCHEMA_VERSION,
                        input_kind=InputKind.SYNTHETIC,
                        synthetic=True,
                        quality=quality,
                        dataset_refs=(_DEMO_DATASET,),
                        feature_vector=vector,
                    )
                )
        return output

    def _aggregate_cell(
        self,
        run: PredictionRun,
        target: str,
        resolution: int,
        horizon: float,
        rows: list[PredictionResult],
        threshold_pm25: float,
    ) -> PredictionCellView:
        expected = h3.cell_to_children_size(target, self.native_resolution)
        area_by_row = {row.h3_cell: h3.cell_area(row.h3_cell, unit="km^2") for row in rows}
        supported = [row for row in rows if row.predicted_pm25 is not None]
        covered_area = sum(area_by_row[row.h3_cell] for row in supported)
        parent_area = h3.cell_area(target, unit="km^2")
        spatial_coverage = min(1.0, covered_area / parent_area) if parent_area else 0.0
        pm25 = _weighted_mean(
            [(row.predicted_pm25, area_by_row[row.h3_cell]) for row in supported]
        )
        baseline = _weighted_mean(
            [(row.baseline_pm25, area_by_row[row.h3_cell]) for row in rows]
        )
        pdi = _weighted_mean([(row.pdi, area_by_row[row.h3_cell]) for row in rows])
        confidence = _weighted_mean(
            [(row.quality.coverage_fraction, area_by_row[row.h3_cell]) for row in supported]
        ) or 0.0
        vector = _aggregate_vector(rows, area_by_row)
        pop_known = [row for row in rows if row.feature_vector.get("population_count") is not None]
        covered_pop = sum(
            float(row.feature_vector["population_count"])
            for row in supported
            if row.feature_vector.get("population_count") is not None
        )
        population_without_pm = sum(
            float(row.feature_vector["population_count"])
            for row in pop_known
            if row.predicted_pm25 is None
        )
        weighted_pop_pm25 = _weighted_mean(
            [
                (
                    row.predicted_pm25,
                    float(row.feature_vector["population_count"]),
                )
                for row in supported
                if row.feature_vector.get("population_count") is not None
                and float(row.feature_vector["population_count"]) > 0
            ]
        )
        population_with_prediction = [
            row
            for row in supported
            if row.feature_vector.get("population_count") is not None
        ]
        residents_over = sum(
            float(row.feature_vector["population_count"])
            for row in population_with_prediction
            if row.predicted_pm25 > threshold_pm25
        ) if population_with_prediction else None
        refs = _unique_refs([ref for row in rows for ref in row.dataset_refs])
        input_kind = (
            InputKind.SYNTHETIC
            if any(row.synthetic for row in rows)
            else InputKind.MODELED
            if horizon > 0 or any(row.input_kind is InputKind.MODELED for row in rows)
            else InputKind.OBSERVED
        )
        methods = {row.prediction_method for row in rows}
        models = {row.model_version for row in rows}
        warnings = sorted({warning for row in rows for warning in row.quality.warnings})
        if spatial_coverage < 0.999:
            warnings.append("Partial native-cell coverage; uncovered area was not upscaled.")
        if not pop_known:
            warnings.append("No population estimates are available for this aggregation.")
        quality = FeatureQuality(
            coverage_fraction=_weighted_mean(
                [(row.quality.coverage_fraction, area_by_row[row.h3_cell]) for row in rows]
            ) or 0.0,
            observed_station_count=sum(row.quality.observed_station_count for row in rows),
            max_observation_age_hours=max(
                (row.quality.max_observation_age_hours or 0 for row in rows), default=0
            ) or None,
            missing_fields=tuple(sorted({name for row in rows for name in row.quality.missing_fields})),
            warnings=tuple(warnings),
        )
        unknown_population = population_without_pm if pop_known else None
        population_ref = next(
            (
                ref.version
                for ref in refs
                if "population" in (ref.dataset_id + ref.product + ref.source).lower()
            ),
            None,
        )
        dataset_version = population_ref
        exposure = ExposureSummary(
            population_weighted_pm25=weighted_pop_pm25,
            residents_above_threshold=residents_over,
            threshold_pm25=threshold_pm25,
            covered_population=covered_pop,
            unknown_population=unknown_population,
            population_dataset_version=dataset_version,
            scope=f"H3 resolution {resolution} cell {target}; threshold is a caller-selected cutoff, not a health limit",
        )
        center_lat, center_lon = cell_center(target)
        native_interval = rows[0] if resolution == self.native_resolution and len(rows) == 1 else None
        return PredictionCellView(
            h3_cell=target,
            resolution=resolution,
            horizon_hours=horizon,
            valid_at=max((row.valid_at for row in rows), default=run.generated_at),
            latitude=center_lat,
            longitude=center_lon,
            pm25=pm25,
            baseline_pm25=baseline,
            lower_pm25=native_interval.lower_pm25 if native_interval is not None else None,
            upper_pm25=native_interval.upper_pm25 if native_interval is not None else None,
            pdi=pdi,
            confidence=confidence,
            spatial_coverage_fraction=spatial_coverage,
            supported_native_cells=len(supported),
            expected_native_cells=expected,
            input_kind=input_kind,
            prediction_method=next(iter(methods)) if len(methods) == 1 else "native-cell aggregate",
            model_version=next(iter(models)) if len(models) == 1 else None,
            feature_schema_version=run.feature_schema_version,
            synthetic=run.mode is DataMode.DEMO or any(row.synthetic for row in rows),
            quality=quality,
            dataset_refs=refs,
            feature_vector=vector,
            exposure=exposure,
        )


def _weighted_mean(values: list[tuple[float | None, float]]) -> float | None:
    present = [(value, weight) for value, weight in values if value is not None and weight > 0]
    denominator = sum(weight for _, weight in present)
    return None if denominator == 0 else sum(value * weight for value, weight in present) / denominator


def _interpolate_number(first: float | None, second: float | None, ratio: float) -> float | None:
    if first is None or second is None:
        return None
    return first + (second - first) * ratio


def _unique_refs(refs: list[DatasetRef]) -> tuple[DatasetRef, ...]:
    by_id = {ref.dataset_id: ref for ref in refs}
    return tuple(by_id[key] for key in sorted(by_id))


def _aggregate_vector(
    rows: list[PredictionResult], area_by_row: dict[str, float]
) -> dict[str, float | None]:
    if not rows:
        return {}
    names = set().union(*(row.feature_vector.keys() for row in rows))
    vector: dict[str, float | None] = {}
    for name in names:
        values = [(row.feature_vector.get(name), area_by_row[row.h3_cell]) for row in rows]
        if name == "population_count":
            known = [float(value) for value, _ in values if value is not None]
            vector[name] = sum(known) if known else None
        elif name.endswith("_fraction") or name in {
            "population_density_per_km2",
            "road_length_km_per_km2",
            "major_road_distance_km",
        }:
            vector[name] = _weighted_mean(values)
        elif name in {"wind_speed_ms", "wind_direction_deg", "wind_u_ms", "wind_v_ms"}:
            continue
        else:
            vector[name] = _weighted_mean(values)

    winds: list[tuple[float, float, float]] = []
    for row in rows:
        vector_data = row.feature_vector
        speed = vector_data.get("wind_speed_ms")
        direction = vector_data.get("wind_direction_deg")
        u = vector_data.get("wind_u_ms")
        v = vector_data.get("wind_v_ms")
        if u is None or v is None:
            if speed is None or direction is None:
                continue
            radians = math.radians(float(direction))
            u = -float(speed) * math.sin(radians)
            v = -float(speed) * math.cos(radians)
        winds.append((float(u), float(v), area_by_row[row.h3_cell]))
    total = sum(weight for _, _, weight in winds)
    if total:
        u = sum(east * weight for east, _, weight in winds) / total
        v = sum(north * weight for _, north, weight in winds) / total
        vector["wind_u_ms"] = u
        vector["wind_v_ms"] = v
        vector["wind_speed_ms"] = math.hypot(u, v)
        vector["wind_direction_deg"] = math.degrees(math.atan2(-u, -v)) % 360
    return vector
