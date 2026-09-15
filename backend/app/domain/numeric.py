"""Tiny, dependency-free numeric helpers shared across services that
would otherwise each define their own copy — `clamp` was previously
byte-for-byte duplicated in app.services.dispersion and
app.services.demo_data, and `clamp01` in app.services.dispersion and
app.services.pdi. Domain, not a service: no imports, no state, safe for
anything to depend on.
"""

from __future__ import annotations


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def clamp01(value: float) -> float:
    """clamp(value, 0.0, 1.0) — its own name because callers normalizing
    a factor to [0, 1] (PDI's inputs, mainly) read better this way than
    a bare clamp(x, 0.0, 1.0) at every call site.
    """
    return clamp(value, 0.0, 1.0)
