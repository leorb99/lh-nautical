"""Allowlisted, deterministic structured queries.

Every statement is assembled by :mod:`src.structured.compiler` from the
reviewed registry (:mod:`src.structured.semantic_model`); user input never
becomes SQL: the only values that vary are the catalog/schema (validated as
plain identifiers, coming from configuration — never from the user) and
bound named parameters (``top_n``, dates, filter values), never interpolated
into the statement text.

``prepare_query`` is the entry point the service uses. The Phase 6B.1 intent
``TOP_PRODUCTS_BY_QUANTITY`` is now just one analytical request
(``RANKING`` + ``TOTAL_QUANTITY_SOLD`` + ``PRODUCT``) compiled by the same
compiler; ``TOP_PRODUCTS_BY_QUANTITY_SQL`` below is kept as the frozen
reference of the statement validated against the real Gold layer in Phase
6B.0 (mirroring the Power BI measures ``Quantidade Total Vendida`` and
``Ranking por Quantidade``), and a regression test proves the compiler still
reproduces it.
"""

from __future__ import annotations

from typing import Any, Mapping

from src.structured.analytical_request import (
    DEFAULT_TOP_N,
    MAX_TOP_N,
    AnalyticalRequest,
)
from src.structured.compiler import compile_request, validate_identifier
from src.structured.models import (
    InvalidParameterError,
    PreparedQuery,
    StructuredIntent,
    UnsupportedIntentError,
)
from src.structured.semantic_model import Dimension, Metric, Operation

__all__ = [
    "DEFAULT_TOP_N",
    "MAX_TOP_N",
    "request_for_intent",
    "FACT_TABLE",
    "DIMENSION_TABLE",
    "TOP_PRODUCTS_BY_QUANTITY_SQL",
    "PreparedQuery",
    "validate_identifier",
    "validate_top_n",
    "prepare_query",
]

FACT_TABLE = "fct_sales"
DIMENSION_TABLE = "dim_products"

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


def validate_top_n(value: object) -> int:
    """``top_n`` must be a real ``int`` (not a bool, float or string) in
    ``1..MAX_TOP_N``."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidParameterError(f"top_n must be an integer, got {type(value).__name__}")
    if value < 1 or value > MAX_TOP_N:
        raise InvalidParameterError(f"top_n must be between 1 and {MAX_TOP_N}, got {value}")
    return value


def request_for_intent(intent: object, params: Mapping[str, Any] | None) -> AnalyticalRequest:
    """The analytical request an intent stands for. ``ANALYTICS`` carries its
    request in ``params["request"]``; the Phase 6B.1 top-products intent is
    the equivalent ranking request. Unknown intents/parameters are rejected."""
    params = dict(params or {})
    if intent is StructuredIntent.TOP_PRODUCTS_BY_QUANTITY:
        unknown = set(params) - {"top_n"}
        if unknown:
            raise InvalidParameterError(f"unknown parameter(s) for {intent.value}: {sorted(unknown)}")
        return AnalyticalRequest(
            metric=Metric.TOTAL_QUANTITY_SOLD,
            operation=Operation.RANKING,
            dimensions=(Dimension.PRODUCT,),
            top_n=validate_top_n(params.get("top_n", DEFAULT_TOP_N)),
        )
    if intent is StructuredIntent.ANALYTICS:
        unknown = set(params) - {"request"}
        if unknown:
            raise InvalidParameterError(f"unknown parameter(s) for {intent.value}: {sorted(unknown)}")
        request = params.get("request")
        if not isinstance(request, AnalyticalRequest):
            raise InvalidParameterError("ANALYTICS requires params['request'] to be an AnalyticalRequest")
        return request
    raise UnsupportedIntentError(f"unsupported structured intent: {intent!r}")


def prepare_query(
    intent: object,
    params: Mapping[str, Any] | None,
    *,
    catalog: str,
    schema: str,
) -> PreparedQuery:
    """Resolve ``intent`` to an analytical request and compile it to the
    allowlisted statement. Unknown intents and parameters are rejected — this
    (through the compiler) is the single place where a request becomes
    executable SQL."""
    return compile_request(
        request_for_intent(intent, params), catalog=catalog, schema=schema, intent=intent  # type: ignore[arg-type]
    )
