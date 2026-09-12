"""A generic wrapper distinguishing real repository data from demo fallback."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class ServiceResult(Generic[T]):
    data: T
    is_demo: bool
