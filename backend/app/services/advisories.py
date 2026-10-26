"""Deterministic advisory drafts grounded in one resolved evidence context."""

from __future__ import annotations

from datetime import UTC

from app.domain.advisories import AdvisoryBundle, AdvisoryContext


def build_deterministic_template(context: AdvisoryContext) -> AdvisoryBundle:
    """Create a clearly labeled draft without introducing or inferring facts."""
    flags = ["DRAFT", context.mode.value.upper()]
    if context.fallback:
        flags.append("FALLBACK")
    if context.synthetic and not context.fallback:
        flags.append("SYNTHETIC INPUT")
    if context.stale:
        flags.append("STALE INPUT")

    freshness = (
        "The effective source-observation age is approximately "
        f"{context.input_age_hours:.1f} hours."
        if context.input_age_hours is not None
        else "The effective source-observation age is unknown."
    )
    summary = (
        f"A published platform-{context.severity} forecast alert "
        f"for H3 cell {context.h3_cell} "
        f"in {context.region} reports PM2.5 of {context.forecast_pm25:.1f} µg/m³ "
        f"at +{context.forecast_hours:g} hours, valid at "
        f"{context.forecast_at.astimezone(UTC).isoformat().replace('+00:00', 'Z')}. {freshness} "
        "This is a forecast, not an independently measured or confirmed pollution event. "
        "It does not identify a pollution source, establish exposure, or determine "
        "an official AQI or GRAP stage."
    )
    checklist = (
        "Check current, independent ground measurements for the same area and time window.",
        "Verify the reported condition and possible source through an authorized field review.",
        "Record the observation time, method, and evidence reference for any follow-up.",
        "Apply the responsible authority's current procedures; this forecast "
        "does not issue an order.",
    )
    return AdvisoryBundle(
        context=context,
        draft_label=" — ".join(flags),
        summary=summary,
        inspection_checklist=checklist,
    )
