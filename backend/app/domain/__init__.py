"""Pure domain types (dataclasses, enums) and future model logic.

This package must never import from app.db, app.ingestion, or app.api.
It has no I/O, so it can be tested and reused without a database or
network access — this is where the pollution/spread model will live.
"""
