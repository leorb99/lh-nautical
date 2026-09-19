"""StructuredQueryService.

Runs one allowlisted analytical request (or the legacy top-products intent,
which is the equivalent request) through an injected
:class:`~src.structured.executor.SqlExecutor` and returns a typed
:class:`~src.structured.models.StructuredResult`.

It never accepts SQL from a caller, never executes an unknown intent, and
never asks a model to write SQL. Every failure becomes a
:class:`~src.structured.models.StructuredQueryError` subclass so callers
have exactly one exception family to contain.
"""

from __future__ import annotations

import re
import time
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping

from src.structured.executor import SqlExecutor
from src.structured.models import (
    AnalyticalTable,
    ColumnRole,
    ColumnSpec,
    ColumnType,
    PreparedQuery,
    StructuredExecutionError,
    StructuredIntent,
    StructuredQueryError,
    StructuredResult,
    TopProductRow,
)
from src.structured.queries import prepare_query, validate_identifier
from src.structured.semantic_model import Operation

__all__ = ["StructuredQueryService", "DEFAULT_SCHEMA"]

DEFAULT_SCHEMA = "gold"

_INTEGER_TEXT = re.compile(r"^-?\d+$")
#: A structured answer is a short, exact table. Anything bigger than this is
#: refused instead of truncated (the SQL API result is never silently cut).
MAX_RESULT_ROWS = 500


def _to_int(value: Any, field: str) -> int:
    """Convert a returned value to ``int`` without ever changing it: ints
    pass through, integer-valued text/Decimal are parsed exactly, and
    anything else (None, bool, float, non-integral) is rejected instead of
    being rounded or coerced."""
    if isinstance(value, bool) or value is None:
        raise StructuredExecutionError(f"{field}: unexpected value {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal) and value == value.to_integral_value():
        return int(value)
    if isinstance(value, str) and _INTEGER_TEXT.match(value.strip()):
        return int(value.strip())
    raise StructuredExecutionError(f"{field}: expected an integer, got {value!r}")


def _to_decimal(value: Any, field: str) -> Decimal:
    """Exact decimal from the API's text (or an int/Decimal); never from a
    binary float, so nothing is rounded on the way in."""
    if isinstance(value, bool) or value is None or isinstance(value, float):
        raise StructuredExecutionError(f"{field}: unexpected value {value!r}")
    if isinstance(value, (int, Decimal)):
        return Decimal(value)
    if isinstance(value, str):
        try:
            return Decimal(value.strip())
        except InvalidOperation:
            pass
    raise StructuredExecutionError(f"{field}: expected a decimal number, got {value!r}")


def _to_date(value: Any, field: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            pass
    raise StructuredExecutionError(f"{field}: expected an ISO date, got {value!r}")


def _parse_value(value: Any, spec: ColumnSpec, index: int) -> Any:
    label = f"row {index}: {spec.name}"
    if value is None:
        if spec.nullable:
            return None
        raise StructuredExecutionError(f"{label} must not be null")
    if spec.type is ColumnType.INT:
        return _to_int(value, label)
    if spec.type is ColumnType.DECIMAL:
        return _to_decimal(value, label)
    if spec.type is ColumnType.DATE:
        return _to_date(value, label)
    if not isinstance(value, str) or (not spec.nullable and value.strip() == ""):
        raise StructuredExecutionError(f"{label} must be a non-empty string, got {value!r}")
    return value


def _normalize_table(raw_rows: Any, prepared: PreparedQuery) -> AnalyticalTable:
    """Validate the executor's rows against the compiled query's column
    specs. Missing columns, wrong types and unexpected NULLs are errors —
    nothing is coerced, rounded or dropped."""
    columns = prepared.columns
    rows: list[tuple[Any, ...]] = []
    for index, raw in enumerate(raw_rows):
        missing = [column.name for column in columns if column.name not in raw]
        if missing:
            raise StructuredExecutionError(f"row {index} is missing column(s): {missing}")
        rows.append(tuple(_parse_value(raw[column.name], column, index) for column in columns))
    if len(rows) > MAX_RESULT_ROWS:
        raise StructuredExecutionError(f"result has {len(rows)} rows, more than the {MAX_RESULT_ROWS} allowed")

    # An aggregate over no rows is NULL: that is "no data", not a value.
    request = prepared.request
    if request is not None and request.operation is Operation.AGGREGATE and len(rows) == 1:
        metric_indexes = [i for i, c in enumerate(columns) if c.role in (ColumnRole.METRIC, ColumnRole.PREVIOUS)]
        if metric_indexes and all(rows[0][i] is None for i in metric_indexes):
            rows = []
    return AnalyticalTable(columns=tuple(columns), rows=tuple(rows))


def _top_product_rows(table: AnalyticalTable) -> tuple[TopProductRow, ...]:
    index = {column.name: position for position, column in enumerate(table.columns)}
    return tuple(
        TopProductRow(
            rank=row[index["ranking"]],
            product_id=row[index["product_id"]],
            product_name=row[index["product_name"]],
            quantity_sold=row[index["quantity_sold"]],
        )
        for row in table.rows
    )


class StructuredQueryService:
    def __init__(
        self,
        executor: SqlExecutor,
        *,
        catalog: str,
        schema: str = DEFAULT_SCHEMA,
        warehouse_id: str | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._executor = executor
        # Validated once, at construction: a bad configuration fails
        # loudly at startup instead of on the first user request.
        self._catalog = validate_identifier(catalog, label="catalog")
        self._schema = validate_identifier(schema, label="schema")
        self._warehouse_id = warehouse_id
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @property
    def catalog(self) -> str:
        return self._catalog

    def execute(self, intent: StructuredIntent, params: Mapping[str, Any] | None = None) -> StructuredResult:
        """Validate ``intent``/``params``, run the allowlisted statement and
        return normalized rows. Raises a :class:`StructuredQueryError`
        subclass on any failure — never anything else."""
        prepared = prepare_query(intent, params, catalog=self._catalog, schema=self._schema)

        started = time.monotonic()
        try:
            raw_rows = self._executor.execute(prepared.statement, prepared.parameters)
        except StructuredQueryError:
            raise
        except Exception as exc:  # noqa: BLE001 — the executor boundary: any
            # transport/warehouse/SDK failure becomes one controlled error.
            raise StructuredExecutionError(f"executor failed: {exc}") from exc
        latency_s = time.monotonic() - started

        table = _normalize_table(raw_rows, prepared)
        legacy_rows = _top_product_rows(table) if intent is StructuredIntent.TOP_PRODUCTS_BY_QUANTITY else ()
        return StructuredResult(
            intent=prepared.intent,
            rows=legacy_rows,
            row_count=len(table.rows),
            source_tables=prepared.source_tables,
            catalog=self._catalog,
            applied_filters=dict(prepared.applied_filters),
            executed_at=self._clock(),
            warehouse_id=self._warehouse_id,
            latency_s=latency_s,
            request=prepared.request,
            table=table,
        )
