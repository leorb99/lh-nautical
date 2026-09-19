"""Allowlisted, deterministic structured queries — Phase 6B.1.

Every statement here is fixed text reviewed by a human. User input never
becomes SQL: the only values that vary are the catalog/schema (validated
as plain identifiers, coming from configuration — never from the user)
and ``top_n`` (a bounded integer bound as a named parameter, never
interpolated into the statement text).

The semantics were validated against the real Gold layer in Phase 6B.0
and mirror the documented Power BI measures ``Quantidade Total Vendida``
(effective sales only) and ``Ranking por Quantidade`` (dense ranking at
product level).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from src.structured.models import InvalidParameterError, StructuredIntent, UnsupportedIntentError

__all__ = [
    "DEFAULT_TOP_N",
    "MAX_TOP_N",
    "FACT_TABLE",
    "DIMENSION_TABLE",
    "TOP_PRODUCTS_BY_QUANTITY_SQL",
    "PreparedQuery",
    "validate_identifier",
    "validate_top_n",
    "prepare_query",
]

#: ``top_n`` counts ranking *positions*, not rows: a tie at the last
#: position returns more than ``top_n`` rows (Phase 6B.0 observed 11 rows
#: for top_n=10 because two products tied at rank 8).
DEFAULT_TOP_N = 10

#: Upper bound for ``top_n``. The product catalog has ~500 products, so 50
#: positions is far more than any chat answer needs while still keeping
#: the generated evidence block small and the query cheap.
MAX_TOP_N = 50

FACT_TABLE = "fct_sales"
DIMENSION_TABLE = "dim_products"

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# NOTE: `WHERE ranking <= :top_n` is intentional — never `LIMIT :top_n`.
# `LIMIT` would cut tied products at the last ranking position.
TOP_PRODUCTS_BY_QUANTITY_SQL = """\
WITH product_sales AS (
    SELECT
        p.product_id,
        p.product_name,
        SUM(s.order_item_quantity) AS quantity_sold
    FROM {catalog}.{schema}.fct_sales AS s
    INNER JOIN {catalog}.{schema}.dim_products AS p
        ON s.product_variant_id = p.product_variant_id
    WHERE s.is_effective_sale = TRUE
    GROUP BY
        p.product_id,
        p.product_name
),
ranked AS (
    SELECT
        product_id,
        product_name,
        quantity_sold,
        DENSE_RANK() OVER (
            ORDER BY quantity_sold DESC
        ) AS ranking
    FROM product_sales
)
SELECT
    ranking,
    product_id,
    product_name,
    quantity_sold
FROM ranked
WHERE ranking <= :top_n
ORDER BY
    ranking,
    product_id,
    product_name
"""


@dataclass(frozen=True)
class PreparedQuery:
    """A ready-to-execute allowlisted statement plus its bound parameters."""

    intent: StructuredIntent
    statement: str
    parameters: Mapping[str, Any]
    applied_filters: Mapping[str, Any]
    source_tables: tuple[str, ...]


def validate_identifier(value: object, *, label: str) -> str:
    """Catalog/schema names must be plain identifiers. They come from
    configuration, but are still validated so a misconfigured value can
    never smuggle SQL into the fixed statement."""
    if not isinstance(value, str) or not _IDENTIFIER_PATTERN.match(value):
        raise InvalidParameterError(f"{label} must be a plain identifier ([A-Za-z_][A-Za-z0-9_]*), got {value!r}")
    return value


def validate_top_n(value: object) -> int:
    """``top_n`` must be a real ``int`` (not a bool, float or string) in
    ``1..MAX_TOP_N``."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidParameterError(f"top_n must be an integer, got {type(value).__name__}")
    if value < 1 or value > MAX_TOP_N:
        raise InvalidParameterError(f"top_n must be between 1 and {MAX_TOP_N}, got {value}")
    return value


def prepare_query(
    intent: object,
    params: Mapping[str, Any] | None,
    *,
    catalog: str,
    schema: str,
) -> PreparedQuery:
    """Select the allowlisted statement for ``intent`` and validate its
    parameters. Unknown intents and unknown parameters are rejected — this
    is the single place where a request becomes executable SQL."""
    if intent is not StructuredIntent.TOP_PRODUCTS_BY_QUANTITY:
        raise UnsupportedIntentError(f"unsupported structured intent: {intent!r}")

    params = dict(params or {})
    unknown = set(params) - {"top_n"}
    if unknown:
        raise InvalidParameterError(f"unknown parameter(s) for {intent.value}: {sorted(unknown)}")

    top_n = validate_top_n(params.get("top_n", DEFAULT_TOP_N))
    catalog = validate_identifier(catalog, label="catalog")
    schema = validate_identifier(schema, label="schema")

    return PreparedQuery(
        intent=intent,
        statement=TOP_PRODUCTS_BY_QUANTITY_SQL.format(catalog=catalog, schema=schema),
        parameters={"top_n": top_n},
        applied_filters={
            "population": "effective_sales",
            "period": "all",
            "location": "all",
            "top_n": top_n,
        },
        source_tables=(f"{schema}.{FACT_TABLE}", f"{schema}.{DIMENSION_TABLE}"),
    )
