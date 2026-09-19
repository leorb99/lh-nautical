"""StructuredQueryService — Phase 6B.1.

Runs one allowlisted analytical intent through an injected
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
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Mapping

from src.structured.executor import SqlExecutor
from src.structured.models import (
    StructuredExecutionError,
    StructuredIntent,
    StructuredQueryError,
    StructuredResult,
    TopProductRow,
)
from src.structured.queries import prepare_query, validate_identifier

__all__ = ["StructuredQueryService", "DEFAULT_SCHEMA"]

DEFAULT_SCHEMA = "gold"

_INTEGER_TEXT = re.compile(r"^-?\d+$")
_EXPECTED_COLUMNS = ("ranking", "product_id", "product_name", "quantity_sold")


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


def _normalize_top_products(raw_rows: Any) -> tuple[TopProductRow, ...]:
    rows: list[TopProductRow] = []
    for index, raw in enumerate(raw_rows):
        missing = [column for column in _EXPECTED_COLUMNS if column not in raw]
        if missing:
            raise StructuredExecutionError(f"row {index} is missing column(s): {missing}")
        name = raw["product_name"]
        if not isinstance(name, str) or name.strip() == "":
            raise StructuredExecutionError(f"row {index}: product_name must be a non-empty string, got {name!r}")
        rows.append(
            TopProductRow(
                rank=_to_int(raw["ranking"], "ranking"),
                product_id=_to_int(raw["product_id"], "product_id"),
                product_name=name,
                quantity_sold=_to_int(raw["quantity_sold"], "quantity_sold"),
            )
        )
    return tuple(rows)


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

        rows = _normalize_top_products(raw_rows)
        return StructuredResult(
            intent=prepared.intent,
            rows=rows,
            row_count=len(rows),
            source_tables=prepared.source_tables,
            catalog=self._catalog,
            applied_filters=dict(prepared.applied_filters),
            executed_at=self._clock(),
            warehouse_id=self._warehouse_id,
            latency_s=latency_s,
        )
