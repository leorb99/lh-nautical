"""Unit tests for src.structured — Phase 6B.1.

No Databricks, no SQL Warehouse, no network: the service is driven by a fake
executor and the concrete Databricks executor by a fake ``WorkspaceClient``.
The fixture rows below are the real Top-10 result validated against the Gold
layer in Phase 6B.0 (11 rows because rank 8 had a tie), returned the way the
SQL API returns them — every value as text.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from databricks.sdk.service.sql import ExecuteStatementRequestOnWaitTimeout, StatementState

from src.structured.config import StructuredDataConfig
from src.structured.executor import DatabricksSqlExecutor, SqlExecutionError
from src.structured.models import (
    InvalidParameterError,
    StructuredExecutionError,
    StructuredIntent,
    StructuredQueryError,
    StructuredResult,
    TopProductRow,
    UnsupportedIntentError,
)
from src.structured.queries import (
    DEFAULT_TOP_N,
    MAX_TOP_N,
    TOP_PRODUCTS_BY_QUANTITY_SQL,
    prepare_query,
    validate_top_n,
)
from src.structured.service import StructuredQueryService

INTENT = StructuredIntent.TOP_PRODUCTS_BY_QUANTITY

VALIDATED_ROWS = [
    ("1", "456", "Colete Salva-Vidas 2374", "1737"),
    ("2", "77", "Bateria Náutica 1945", "1731"),
    ("3", "110", "Hélice de Alumínio 1155", "1705"),
    ("4", "29", "Defensa Náutica 3153", "1687"),
    ("5", "268", "Motor de Popa 9800", "1669"),
    ("6", "408", "Sonar Transducer 7193", "1646"),
    ("7", "312", "Âncora Bruce 831", "1641"),
    ("8", "55", "GPS Plotter 6249", "1638"),
    ("8", "81", "Motor de Popa 7678", "1638"),
    ("9", "311", "GPS Plotter 2249", "1637"),
    ("10", "411", "Motor de Popa 684", "1629"),
]


def _raw_rows(rows=VALIDATED_ROWS):
    return [
        {"ranking": r, "product_id": pid, "product_name": name, "quantity_sold": q} for r, pid, name, q in rows
    ]


class FakeExecutor:
    def __init__(self, rows=None, exc: Exception | None = None):
        self.rows = _raw_rows() if rows is None else rows
        self.exc = exc
        self.calls: list[tuple[str, dict]] = []

    def execute(self, statement, parameters=None):
        self.calls.append((statement, dict(parameters or {})))
        if self.exc is not None:
            raise self.exc
        return self.rows


def _service(executor=None, **kwargs):
    return StructuredQueryService(
        executor or FakeExecutor(), catalog="test_catalog", schema="gold", warehouse_id="wh-1", **kwargs
    )


# ---------------------------------------------------------------------------
# top_n validation
# ---------------------------------------------------------------------------


def test_default_top_n_is_10():
    executor = FakeExecutor()
    result = _service(executor).execute(INTENT)

    assert DEFAULT_TOP_N == 10
    assert executor.calls[0][1] == {"top_n": 10}
    assert result.applied_filters["top_n"] == 10


def test_valid_custom_top_n_is_bound_as_a_parameter():
    executor = FakeExecutor()
    _service(executor).execute(INTENT, {"top_n": 5})

    assert executor.calls[0][1] == {"top_n": 5}


@pytest.mark.parametrize("bad", [0, -1, -10, MAX_TOP_N + 1, 10_000, True, False, "10", 5.0, None])
def test_invalid_top_n_is_rejected_before_execution(bad):
    executor = FakeExecutor()

    with pytest.raises(InvalidParameterError):
        _service(executor).execute(INTENT, {"top_n": bad})

    assert executor.calls == []


def test_top_n_bounds_are_inclusive():
    assert validate_top_n(1) == 1
    assert validate_top_n(MAX_TOP_N) == MAX_TOP_N


def test_unknown_parameters_are_rejected():
    executor = FakeExecutor()
    with pytest.raises(InvalidParameterError):
        _service(executor).execute(INTENT, {"top_n": 5, "sql": "DROP TABLE x"})
    assert executor.calls == []


# ---------------------------------------------------------------------------
# allowlisted SQL
# ---------------------------------------------------------------------------


def test_only_the_allowlisted_statement_is_executed():
    executor = FakeExecutor()
    _service(executor).execute(INTENT, {"top_n": 3})

    statement, _ = executor.calls[0]
    assert statement == TOP_PRODUCTS_BY_QUANTITY_SQL.format(catalog="test_catalog", schema="gold")


def test_sql_uses_dense_rank():
    assert "DENSE_RANK() OVER" in TOP_PRODUCTS_BY_QUANTITY_SQL
    assert "ORDER BY quantity_sold DESC" in TOP_PRODUCTS_BY_QUANTITY_SQL


def test_sql_keeps_ties_by_filtering_on_ranking_not_limit():
    assert "WHERE ranking <= :top_n" in TOP_PRODUCTS_BY_QUANTITY_SQL
    assert "LIMIT" not in TOP_PRODUCTS_BY_QUANTITY_SQL.upper()


def test_top_n_value_is_never_interpolated_into_the_statement():
    executor = FakeExecutor()
    _service(executor).execute(INTENT, {"top_n": 7})

    statement, params = executor.calls[0]
    assert ":top_n" in statement
    assert "<= 7" not in statement
    assert params == {"top_n": 7}


def test_sql_filters_effective_sales_only():
    assert "WHERE s.is_effective_sale = TRUE" in TOP_PRODUCTS_BY_QUANTITY_SQL


def test_sql_joins_fact_to_dimension_on_product_variant_id():
    assert "FROM {catalog}.{schema}.fct_sales AS s" in TOP_PRODUCTS_BY_QUANTITY_SQL
    assert "INNER JOIN {catalog}.{schema}.dim_products AS p" in TOP_PRODUCTS_BY_QUANTITY_SQL
    assert "ON s.product_variant_id = p.product_variant_id" in TOP_PRODUCTS_BY_QUANTITY_SQL


def test_sql_aggregates_at_product_grain_with_the_validated_metric():
    assert "SUM(s.order_item_quantity) AS quantity_sold" in TOP_PRODUCTS_BY_QUANTITY_SQL
    assert "GROUP BY\n        p.product_id,\n        p.product_name" in TOP_PRODUCTS_BY_QUANTITY_SQL
    assert "ORDER BY\n    ranking,\n    product_id,\n    product_name" in TOP_PRODUCTS_BY_QUANTITY_SQL


def test_catalog_and_schema_come_from_configuration():
    executor = FakeExecutor()
    StructuredQueryService(executor, catalog="another_catalog", schema="gold_v2").execute(INTENT)

    statement, _ = executor.calls[0]
    assert "another_catalog.gold_v2.fct_sales" in statement
    assert "another_catalog.gold_v2.dim_products" in statement


@pytest.mark.parametrize("bad", ["x; DROP TABLE t", "a.b", "", "1abc", "cat-name", "cat name", None])
def test_invalid_catalog_or_schema_fails_at_construction(bad):
    with pytest.raises(InvalidParameterError):
        StructuredQueryService(FakeExecutor(), catalog=bad)
    with pytest.raises(InvalidParameterError):
        StructuredQueryService(FakeExecutor(), catalog="ok", schema=bad)


def test_structured_package_hardcodes_no_environment_catalog():
    package = Path(__file__).resolve().parents[1] / "src" / "structured"
    for path in package.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "prod_lh_nautical" not in text, path.name
        assert "dev_natalia" not in text, path.name


# ---------------------------------------------------------------------------
# unsupported intents
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_intent", ["TOP_PRODUCTS_BY_QUANTITY", "DROP TABLE x", None, object(), 1])
def test_unsupported_intent_is_rejected_and_nothing_is_executed(bad_intent):
    executor = FakeExecutor()

    with pytest.raises(UnsupportedIntentError):
        _service(executor).execute(bad_intent)

    assert executor.calls == []


def test_prepare_query_rejects_unknown_intent_directly():
    with pytest.raises(UnsupportedIntentError):
        prepare_query("anything", {}, catalog="c", schema="gold")


# ---------------------------------------------------------------------------
# result normalization
# ---------------------------------------------------------------------------


def test_rows_are_normalized_exactly_from_text_values():
    result = _service().execute(INTENT)

    assert result.rows[0] == TopProductRow(rank=1, product_id=456, product_name="Colete Salva-Vidas 2374", quantity_sold=1737)
    assert all(isinstance(r.rank, int) and isinstance(r.product_id, int) and isinstance(r.quantity_sold, int) for r in result.rows)
    assert [r.quantity_sold for r in result.rows] == [1737, 1731, 1705, 1687, 1669, 1646, 1641, 1638, 1638, 1637, 1629]


def test_large_values_are_preserved_exactly():
    big = 9_007_199_254_740_993  # 2**53 + 1: would be corrupted by a float round-trip
    rows = _raw_rows([("1", "1", "Produto", str(big))])

    result = _service(FakeExecutor(rows)).execute(INTENT)

    assert result.rows[0].quantity_sold == big


def test_int_and_integral_decimal_values_are_accepted():
    rows = [{"ranking": 1, "product_id": Decimal("7"), "product_name": "P", "quantity_sold": Decimal("12")}]
    row = _service(FakeExecutor(rows)).execute(INTENT).rows[0]

    assert (row.rank, row.product_id, row.quantity_sold) == (1, 7, 12)


def test_ties_are_preserved_and_may_exceed_top_n_rows():
    result = _service().execute(INTENT, {"top_n": 10})

    assert result.row_count == 11 > 10
    tied = [r for r in result.rows if r.rank == 8]
    assert [r.product_id for r in tied] == [55, 81]
    assert [r.rank for r in result.rows] == [1, 2, 3, 4, 5, 6, 7, 8, 8, 9, 10]


def test_empty_result_is_a_valid_empty_structured_result():
    result = _service(FakeExecutor(rows=[])).execute(INTENT)

    assert result.rows == ()
    assert result.row_count == 0


def test_result_carries_metadata():
    fixed = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
    result = _service(clock=lambda: fixed).execute(INTENT, {"top_n": 4})

    assert result.intent is INTENT
    assert result.catalog == "test_catalog"
    assert result.source_tables == ("gold.fct_sales", "gold.dim_products")
    assert result.executed_at == fixed
    assert result.warehouse_id == "wh-1"
    assert result.latency_s is not None and result.latency_s >= 0
    assert result.applied_filters == {"population": "effective_sales", "period": "all", "location": "all", "top_n": 4}


@pytest.mark.parametrize(
    "bad_row",
    [
        {"ranking": "1", "product_id": "1", "product_name": "P"},  # missing column
        {"ranking": "1", "product_id": "1", "product_name": None, "quantity_sold": "5"},
        {"ranking": "1", "product_id": "1", "product_name": "  ", "quantity_sold": "5"},
        {"ranking": "1", "product_id": None, "product_name": "P", "quantity_sold": "5"},
        {"ranking": "1", "product_id": "1", "product_name": "P", "quantity_sold": "1.5"},
        {"ranking": "1", "product_id": "1", "product_name": "P", "quantity_sold": 1.5},
        {"ranking": "1", "product_id": "1", "product_name": "P", "quantity_sold": True},
        {"ranking": "abc", "product_id": "1", "product_name": "P", "quantity_sold": "5"},
    ],
)
def test_malformed_rows_are_rejected_not_repaired(bad_row):
    with pytest.raises(StructuredExecutionError):
        _service(FakeExecutor(rows=[bad_row])).execute(INTENT)


# ---------------------------------------------------------------------------
# failure handling
# ---------------------------------------------------------------------------


def test_executor_failure_becomes_a_controlled_error():
    boom = RuntimeError("warehouse unavailable")

    with pytest.raises(StructuredExecutionError) as excinfo:
        _service(FakeExecutor(exc=boom)).execute(INTENT)

    assert isinstance(excinfo.value, StructuredQueryError)
    assert excinfo.value.__cause__ is boom


def test_only_structured_query_errors_escape_the_service():
    for exc in (RuntimeError("x"), ValueError("y"), SqlExecutionError("z"), KeyError("k")):
        with pytest.raises(StructuredQueryError):
            _service(FakeExecutor(exc=exc)).execute(INTENT)


# ---------------------------------------------------------------------------
# DatabricksSqlExecutor (fake client — never a live warehouse)
# ---------------------------------------------------------------------------


def _response(state=StatementState.SUCCEEDED, columns=("ranking", "product_id"), data=(("1", "9"),), truncated=False, error=None):
    return SimpleNamespace(
        status=SimpleNamespace(state=state, error=error),
        manifest=SimpleNamespace(
            schema=SimpleNamespace(columns=[SimpleNamespace(name=c) for c in columns]), truncated=truncated
        ),
        result=SimpleNamespace(data_array=[list(r) for r in data]),
    )


def _client(response):
    client = MagicMock()
    client.statement_execution.execute_statement.return_value = response
    return client


def test_databricks_executor_sends_a_typed_named_parameter_and_cancels_on_timeout():
    client = _client(_response())
    rows = DatabricksSqlExecutor(client, "wh-1").execute("SELECT 1 WHERE x <= :top_n", {"top_n": 10})

    kwargs = client.statement_execution.execute_statement.call_args.kwargs
    assert kwargs["warehouse_id"] == "wh-1"
    assert kwargs["statement"] == "SELECT 1 WHERE x <= :top_n"
    assert kwargs["on_wait_timeout"] is ExecuteStatementRequestOnWaitTimeout.CANCEL
    (parameter,) = kwargs["parameters"]
    assert (parameter.name, parameter.value, parameter.type) == ("top_n", "10", "INT")
    assert rows == [{"ranking": "1", "product_id": "9"}]


def test_databricks_executor_maps_rows_to_column_names():
    client = _client(_response(columns=("a", "b"), data=(("1", "x"), ("2", None))))
    assert DatabricksSqlExecutor(client, "wh").execute("q") == [{"a": "1", "b": "x"}, {"a": "2", "b": None}]


@pytest.mark.parametrize("state", [StatementState.FAILED, StatementState.CANCELED, StatementState.PENDING, StatementState.CLOSED])
def test_databricks_executor_raises_when_the_statement_did_not_succeed(state):
    client = _client(_response(state=state, error=SimpleNamespace(message="boom")))
    with pytest.raises(SqlExecutionError):
        DatabricksSqlExecutor(client, "wh").execute("q")


def test_databricks_executor_never_returns_a_truncated_result():
    with pytest.raises(SqlExecutionError):
        DatabricksSqlExecutor(_client(_response(truncated=True)), "wh").execute("q")


@pytest.mark.parametrize("bad", [1.5, True, None, ["a"]])
def test_databricks_executor_rejects_unsupported_parameter_types(bad):
    with pytest.raises(SqlExecutionError):
        DatabricksSqlExecutor(_client(_response()), "wh").execute("q", {"p": bad})


def test_service_wraps_databricks_executor_failures_end_to_end():
    client = _client(_response(state=StatementState.FAILED, error=SimpleNamespace(message="no access")))
    service = StructuredQueryService(DatabricksSqlExecutor(client, "wh"), catalog="test_catalog")

    with pytest.raises(StructuredExecutionError):
        service.execute(INTENT)


def test_service_works_end_to_end_over_the_databricks_executor_shape():
    client = _client(
        _response(
            columns=("ranking", "product_id", "product_name", "quantity_sold"),
            data=[(r, pid, name, q) for r, pid, name, q in VALIDATED_ROWS],
        )
    )
    result = StructuredQueryService(DatabricksSqlExecutor(client, "wh"), catalog="test_catalog").execute(INTENT)

    assert isinstance(result, StructuredResult)
    assert result.row_count == 11
    assert result.rows[7].product_id == 55 and result.rows[8].product_id == 81


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------


def test_config_is_disabled_without_a_warehouse():
    assert StructuredDataConfig.from_env({}, default_catalog="c") is None
    assert StructuredDataConfig.from_env({"SQL_WAREHOUSE_ID": "   "}, default_catalog="c") is None


def test_config_defaults_follow_the_existing_catalog_and_gold_schema():
    config = StructuredDataConfig.from_env({"SQL_WAREHOUSE_ID": "wh-1"}, default_catalog="bundle_catalog")

    assert config == StructuredDataConfig(catalog="bundle_catalog", schema="gold", warehouse_id="wh-1")


def test_config_environment_overrides_win():
    env = {"SQL_WAREHOUSE_ID": "wh-1", "DATA_CATALOG": "other_catalog", "DATA_SCHEMA": "gold_v2"}
    config = StructuredDataConfig.from_env(env, default_catalog="bundle_catalog")

    assert (config.catalog, config.schema) == ("other_catalog", "gold_v2")
