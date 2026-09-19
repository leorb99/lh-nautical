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
    "ColumnType",
    "ColumnRole",
    "ColumnSpec",
    "AnalyticalTable",
    "PreparedQuery",
    "TopProductRow",
    "StructuredResult",
    "StructuredQueryError",
    "UnsupportedIntentError",
    "UnsupportedRequestError",
    "InvalidParameterError",
    "StructuredExecutionError",
]


class StructuredIntent(Enum):
    """Allowlist of analytical operations the structured layer can run.

    ``ANALYTICS`` carries a typed :class:`~src.structured.analytical_request.
    AnalyticalRequest` (approved metric / operation / dimensions / filters)
    that a deterministic compiler turns into SQL — adding a metric is a
    registry entry, not a new intent. ``TOP_PRODUCTS_BY_QUANTITY`` is the
    Phase 6B.1 intent, kept for compatibility: it is expressed as the
    equivalent analytical request and compiled by the same code.
    Neither ever involves free-form SQL generation.
    """

    TOP_PRODUCTS_BY_QUANTITY = "TOP_PRODUCTS_BY_QUANTITY"
    ANALYTICS = "ANALYTICS"


class StructuredQueryError(Exception):
    """Base class for every controlled structured-data failure. The
    Harness catches this (and only this family) at its structured
    boundary; messages are for server-side logs, never for end users."""


class UnsupportedIntentError(StructuredQueryError):
    """The requested intent is not in the allowlist."""


class UnsupportedRequestError(UnsupportedIntentError):
    """An analytical request combines approved parts in a way the semantic
    model does not allow (invalid metric/dimension/filter/operation
    combination). Fail-closed: nothing is compiled or executed."""


class InvalidParameterError(StructuredQueryError):
    """A parameter is missing, of the wrong type, or out of bounds."""


class StructuredExecutionError(StructuredQueryError):
    """The executor failed, or returned rows that do not match the
    intent's expected shape."""


class ColumnType(Enum):
    INT = "INT"
    DECIMAL = "DECIMAL"
    TEXT = "TEXT"
    DATE = "DATE"


class ColumnRole(Enum):
    RANK = "RANK"
    DIMENSION = "DIMENSION"
    PERIOD = "PERIOD"
    METRIC = "METRIC"
    NUMERATOR = "NUMERATOR"
    DENOMINATOR = "DENOMINATOR"
    PREVIOUS = "PREVIOUS"
    CHANGE = "CHANGE"


@dataclass(frozen=True)
class ColumnSpec:
    """One output column of a compiled analytical query: how to validate
    and parse the value the SQL API returns (always text) and what it means."""

    name: str
    type: ColumnType
    role: ColumnRole
    nullable: bool = False


@dataclass(frozen=True)
class AnalyticalTable:
    """Validated, typed rows of an analytical query. Values are exactly what
    the query returned (``int``/``Decimal``/``str``/``None``) — never rounded
    or reinterpreted here."""

    columns: tuple[ColumnSpec, ...]
    rows: tuple[tuple[Any, ...], ...]

    def column_index(self, name: str) -> int:
        return [column.name for column in self.columns].index(name)


@dataclass(frozen=True)
class PreparedQuery:
    """A ready-to-execute allowlisted statement plus its bound parameters."""

    intent: StructuredIntent
    statement: str
    parameters: Mapping[str, Any]
    applied_filters: Mapping[str, Any]
    source_tables: tuple[str, ...]
    #: The analytical request this statement was compiled from, and the
    #: shape of its result columns (``None``/empty only for hand-built
    #: queries in tests).
    request: Any = None
    columns: tuple[ColumnSpec, ...] = ()


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
    #: Analytical-request results (``rows`` is then only filled for the legacy
    #: ``TOP_PRODUCTS_BY_QUANTITY`` intent): the request that produced them
    #: and the typed result table the renderer turns into the answer.
    request: Any = None
    table: AnalyticalTable | None = None
