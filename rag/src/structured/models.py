"""Structured-data domain models — Phase 6B.1.

Internal only: nothing in this module is part of the public ``/api/ask``
schema. A :class:`StructuredResult` is what the Harness renders into the
final answer text (see :mod:`src.structured.rendering`); it is deliberately
separate from the documentary evidence (``[Sn]`` sources) so the two
provenances are never conflated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

__all__ = [
    "StructuredIntent",
    "TopProductRow",
    "StructuredResult",
    "StructuredQueryError",
    "UnsupportedIntentError",
    "InvalidParameterError",
    "StructuredExecutionError",
]


class StructuredIntent(Enum):
    """Allowlist of analytical operations the structured layer can run.

    One value only in Phase 6B.1. Adding an intent means adding a fixed,
    reviewed query for it — never free-form SQL generation.
    """

    TOP_PRODUCTS_BY_QUANTITY = "TOP_PRODUCTS_BY_QUANTITY"


class StructuredQueryError(Exception):
    """Base class for every controlled structured-data failure. The
    Harness catches this (and only this family) at its structured
    boundary; messages are for server-side logs, never for end users."""


class UnsupportedIntentError(StructuredQueryError):
    """The requested intent is not in the allowlist."""


class InvalidParameterError(StructuredQueryError):
    """A parameter is missing, of the wrong type, or out of bounds."""


class StructuredExecutionError(StructuredQueryError):
    """The executor failed, or returned rows that do not match the
    intent's expected shape."""


@dataclass(frozen=True)
class TopProductRow:
    """One row of ``TOP_PRODUCTS_BY_QUANTITY``. ``rank`` is a *dense*
    rank: tied products share it and the next rank has no gap."""

    rank: int
    product_id: int
    product_name: str
    quantity_sold: int


@dataclass(frozen=True)
class StructuredResult:
    """Outcome of one allowlisted structured query.

    ``row_count`` is the number of returned rows, which can exceed the
    requested ``top_n``: ``top_n`` counts ranking positions, so every
    product tied at the last position is kept.
    """

    intent: StructuredIntent
    rows: tuple[TopProductRow, ...]
    row_count: int
    source_tables: tuple[str, ...]
    catalog: str
    applied_filters: Mapping[str, Any] = field(default_factory=dict)
    executed_at: datetime | None = None
    warehouse_id: str | None = None
    latency_s: float | None = None
