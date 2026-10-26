"""Persisted, consent-gated Gemini advisory generation with a safe fallback."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings
from app.domain.advisories import (
    ADVISORY_SCHEMA_VERSION,
    AdvisoryBundle,
    AdvisoryContext,
    AdvisoryRecord,
)
from app.services.advisories import build_deterministic_template
from app.services.advisory_context import AdvisoryContextService
from app.services.gemini_advisories import (
    PROMPT_VERSION,
    SCHEMA_VERSION,
    GeminiAdvisoryDisabled,
    GeminiAdvisoryError,
    GeminiAdvisoryGenerator,
    GeminiAdvisoryInvalidApiKey,
    GeminiAdvisoryInvalidConfiguration,
    GeminiAdvisoryInvalidOutput,
    GeminiAdvisoryQuotaExceeded,
    GeminiAdvisoryTimeout,
    GeminiTextAdvisoryGenerator,
    build_prompt,
)
from app.services.provider_budget import Reservation

TEMPLATE_PROMPT_VERSION = "deterministic-template-1"


class AdvisoryRepository(Protocol):
    def get(self, bundle_id: str) -> AdvisoryRecord | None: ...

    def get_by_hash(self, context_hash: str) -> AdvisoryRecord | None: ...

    def claim(self, **kwargs: Any) -> Any: ...

    def complete(self, **kwargs: Any) -> AdvisoryRecord | None: ...


class AdvisoryBudget(Protocol):
    def reserve_request_sync(self, **kwargs: Any) -> Reservation: ...

    def complete_request_sync(self, **kwargs: Any) -> None: ...


class AdvisoryNotFound(LookupError):
    pass


class AdvisoryGenerationInProgress(RuntimeError):
    pass


class AdvisoryContextChanged(RuntimeError):
    pass


class AdvisoryGenerationService:
    def __init__(
        self,
        *,
        settings: Settings,
        contexts: AdvisoryContextService,
        repository: AdvisoryRepository,
        budget: AdvisoryBudget,
        generator: GeminiAdvisoryGenerator | None = None,
    ) -> None:
        self._settings = settings
        self._contexts = contexts
        self._repository = repository
        self._budget = budget
        self._generator = generator or GeminiTextAdvisoryGenerator(settings)

    def generate(
        self,
        *,
        alert_id: str,
        consent: bool,
        reanalyze: bool = False,
        now: datetime | None = None,
    ) -> AdvisoryRecord:
        moment = now or datetime.now(UTC)
        if moment.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        context = self._contexts.for_published_alert(alert_id, now=moment)
        template = build_deterministic_template(context)
        wants_gemini = consent and self._settings.gemini_api_key is not None
        generation_key = _generation_key(
            wants_gemini=wants_gemini,
            consent=consent,
            model_id=self._settings.gemini_model,
        )
        prompt_version = PROMPT_VERSION if wants_gemini else TEMPLATE_PROMPT_VERSION
        schema_version = SCHEMA_VERSION if wants_gemini else ADVISORY_SCHEMA_VERSION
        context_hash = _context_hash(context, generation_key)

        cached = self._repository.get_by_hash(context_hash)
        if cached is not None and not reanalyze:
            return cached
        cooldown = timedelta(
            seconds=self._settings.gemini_reanalysis_cooldown_seconds
        )
        if cached is not None and reanalyze and moment - cached.generated_at < cooldown:
            return cached

        claim = self._repository.claim(
            context=context,
            context_hash=context_hash,
            generation_key=generation_key,
            prompt_version=prompt_version,
            schema_version=schema_version,
            now=moment,
            force=bool(cached is not None and reanalyze),
        )
        if claim.state == "cached" and claim.record is not None:
            return claim.record
        if claim.state != "claimed" or claim.lease_token is None:
            raise AdvisoryGenerationInProgress(
                "an identical advisory is already being generated; retry shortly"
            )

        bundle = template
        consented_at: datetime | None = None
        if not consent:
            bundle = replace(
                template,
                prompt_version=TEMPLATE_PROMPT_VERSION,
                template_version=ADVISORY_SCHEMA_VERSION,
            )
        elif self._settings.gemini_api_key is None:
            bundle = _template_fallback(
                template,
                "Gemini is not configured; deterministic template used.",
            )
        else:
            bundle, consented_at = self._generate_with_gemini(
                context=context,
                template=template,
                context_hash=context_hash,
                now=moment,
            )

        record = self._repository.complete(
            context_hash=context_hash,
            lease_token=claim.lease_token,
            bundle=bundle,
            generated_at=moment,
            consented_at=consented_at,
            generation_key=generation_key,
            prompt_version=prompt_version,
            schema_version=schema_version,
        )
        if record is None:
            raise AdvisoryGenerationInProgress(
                "the advisory generation lease expired; retry to use the saved result"
            )
        return record

    def get(self, bundle_id: str, *, now: datetime | None = None) -> AdvisoryRecord:
        record = self._repository.get(bundle_id)
        if record is None:
            raise AdvisoryNotFound("saved advisory was not found")
        context = self._contexts.for_published_alert(
            record.bundle.context.source_id,
            now=now or datetime.now(UTC),
        )
        if _context_hash(context, record.generation_key) != record.context_hash:
            raise AdvisoryContextChanged(
                "the published alert evidence has changed; generate a new advisory"
            )
        return record

    def _generate_with_gemini(
        self,
        *,
        context: AdvisoryContext,
        template: AdvisoryBundle,
        context_hash: str,
        now: datetime,
    ) -> tuple[AdvisoryBundle, datetime | None]:
        prompt = build_prompt(context)
        if len(prompt) > self._settings.gemini_advisory_max_input_chars:
            return (
                _template_fallback(
                    template,
                    "The evidence exceeds the configured Gemini input limit; template used.",
                ),
                None,
            )
        try:
            reservation = self._budget.reserve_request_sync(
                provider="gemini",
                request_key_hash=context_hash,
                request_units=1,
                now=now,
            )
        except SQLAlchemyError:
            return (
                _template_fallback(
                    template,
                    "The shared Gemini budget could not be checked; template used.",
                ),
                None,
            )
        except Exception:
            return (
                _template_fallback(
                    template,
                    "The shared Gemini budget could not be checked; template used.",
                ),
                None,
            )
        if not reservation.allowed or reservation.reservation_id is None:
            return (
                _template_fallback(
                    template,
                    "The application Gemini request budget is exhausted; template used.",
                ),
                None,
            )

        sent_at = datetime.now(UTC)
        status_code = 200
        try:
            output = self._generator.generate(context)
            allowed_ids = set(template.evidence_ids)
            generated_ids = set(output.evidence_ids)
            required_forecast_id = f"alert:{context.source_id}:forecast"
            if (
                not generated_ids.issubset(allowed_ids)
                or required_forecast_id not in generated_ids
            ):
                raise GeminiAdvisoryInvalidOutput(
                    "Gemini returned evidence references outside the resolved context"
                )
            return (
                replace(
                    template,
                    summary=output.summary,
                    inspection_checklist=tuple(output.inspection_checklist),
                    provider_status="gemini",
                    model_id=self._settings.gemini_model,
                    prompt_version=PROMPT_VERSION,
                    provider_message=None,
                    generated_evidence_ids=tuple(output.evidence_ids),
                    template_version=SCHEMA_VERSION,
                ),
                sent_at,
            )
        except GeminiAdvisoryError as exc:
            status_code = _provider_status_code(exc)
            return _template_fallback(template, _safe_fallback_message(exc)), sent_at
        except Exception:
            status_code = 599
            return (
                _template_fallback(
                    template,
                    "Gemini returned an unusable advisory; deterministic template used.",
                ),
                sent_at,
            )
        finally:
            try:
                self._budget.complete_request_sync(
                    reservation_id=reservation.reservation_id,
                    status_code=status_code,
                    bytes_used=len(prompt.encode("utf-8")),
                    now=datetime.now(UTC),
                )
            except Exception:
                # The reservation remains counted even if completion telemetry
                # cannot be written; a usage-write issue must not lose the draft.
                pass


def _generation_key(*, wants_gemini: bool, consent: bool, model_id: str) -> str:
    if wants_gemini:
        return f"gemini:{model_id}:{PROMPT_VERSION}:{SCHEMA_VERSION}"
    if consent:
        return "template-fallback:gemini-disabled:advisory-v1"
    return "template:advisory-v1"


def _context_hash(context: AdvisoryContext, generation_key: str) -> str:
    """Hash stable evidence and freshness state, excluding minute-by-minute age."""
    payload = {
        "source_id": context.source_id,
        "run_id": context.run_id,
        "cell": context.h3_cell,
        "region": context.region,
        "mode": context.mode.value,
        "severity": context.severity,
        "generated_at": context.generated_at.isoformat(),
        "forecast_at": context.forecast_at.isoformat(),
        "forecast_hours": context.forecast_hours,
        "forecast_pm25": context.forecast_pm25,
        "current_pm25": context.current_pm25,
        "confidence": context.confidence,
        "fallback": context.fallback,
        "synthetic": context.synthetic,
        "stale": context.stale,
        "facts": [
            {"id": fact.evidence_id, "label": fact.label, "value": fact.value}
            for fact in context.facts
        ],
        "sources": [
            {"id": source.evidence_id, "source": source.source, "description": source.description}
            for source in context.sources
        ],
        "missing": list(context.missing_evidence),
        "generation_key": generation_key,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _template_fallback(template: AdvisoryBundle, message: str) -> AdvisoryBundle:
    label = template.draft_label
    if "TEMPLATE FALLBACK" not in label:
        label = f"{label} — TEMPLATE FALLBACK"
    return replace(
        template,
        draft_label=label,
        provider_status="template_fallback",
        prompt_version=PROMPT_VERSION,
        provider_message=message,
    )


def _safe_fallback_message(exc: GeminiAdvisoryError) -> str:
    if isinstance(exc, GeminiAdvisoryDisabled):
        return "Gemini is unavailable; deterministic template used."
    if isinstance(exc, GeminiAdvisoryQuotaExceeded):
        return "Gemini quota is unavailable; deterministic template used."
    if isinstance(exc, GeminiAdvisoryInvalidOutput):
        return "Gemini output failed safety validation; deterministic template used."
    return "Gemini generation failed; deterministic template used."


def _provider_status_code(exc: GeminiAdvisoryError) -> int:
    if isinstance(exc, GeminiAdvisoryQuotaExceeded):
        return 429
    if isinstance(exc, GeminiAdvisoryInvalidApiKey):
        return 403
    if isinstance(exc, GeminiAdvisoryInvalidOutput):
        return 400
    if isinstance(exc, GeminiAdvisoryInvalidConfiguration):
        return 400
    if isinstance(exc, GeminiAdvisoryDisabled):
        return 503
    if isinstance(exc, GeminiAdvisoryTimeout):
        return 599
    return 599
