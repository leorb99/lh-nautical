"""Compiler tests: AnalyticalRequest -> allowlisted SQL + named parameters.

The generated SQL is only ever compiled to a string here — nothing is
executed, and no Warehouse or network is involved. The tests pin the
business semantics (effective sales, grain, ties, order-level amounts) and
the safety properties (named parameters only, allowlisted tables, no
``LIMIT``, validated identifiers).
"""

from __future__ import annotations

import re
from datetime import date

import pytest

from src.structured.analytical_request import (
    AnalyticalRequest,
    DateRange,
    RequestFilters,
    validate_request,
)
from src.structured.compiler import compile_request, validate_identifier
from src.structured.executor import _parameter
from src.structured.models import InvalidParameterError, StructuredIntent, UnsupportedRequestError
from src.structured.queries import TOP_PRODUCTS_BY_QUANTITY_SQL, prepare_query
from src.structured.semantic_model import (
    DIMENSIONS,
    FACTS,
    METRICS,
    Dimension,
    FilterKind,
    Metric,
    Operation,
    TimeGrain,
    TimeModifier,
)

CATALOG, SCHEMA = "test_catalog", "gold"
YEAR_2025 = DateRange(date(2025, 1, 1), date(2025, 12, 31))
ALLOWED_TABLES = {fact.table for fact in FACTS.values()} | {
    binding.join.table for fact in FACTS.values() for binding in fact.bindings.values() if binding.join
}


def compile_(request: AnalyticalRequest):
    return compile_request(request, catalog=CATALOG, schema=SCHEMA)


def _req(metric, operation=Operation.AGGREGATE, dimensions=(), **kwargs) -> AnalyticalRequest:
    return AnalyticalRequest(metric=metric, operation=operation, dimensions=tuple(dimensions), **kwargs)


def _norm(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


# ---------------------------------------------------------------------------
# examples (compiled only)
# ---------------------------------------------------------------------------


def test_net_revenue_aggregate():
    query = compile_(_req(Metric.NET_REVENUE))

    assert query.statement == (
        "SELECT\n"
        "    SUM(s.order_item_net_revenue_after_returns) AS net_revenue\n"
        "FROM test_catalog.gold.fct_sales AS s\n"
        "WHERE s.is_effective_sale = TRUE\n"
    )
    assert query.parameters == {}
    assert query.source_tables == ("gold.fct_sales",)
    assert query.applied_filters == {"population": "effective_sales", "period": "all", "location": "all"}


def test_top_products_by_revenue():
    query = compile_(_req(Metric.NET_REVENUE, Operation.RANKING, [Dimension.PRODUCT], top_n=5))
    sql = _norm(query.statement)

    assert "SUM(s.order_item_net_revenue_after_returns) AS net_revenue" in sql
    assert "DENSE_RANK() OVER ( ORDER BY net_revenue DESC ) AS ranking" in sql
    assert "GROUP BY p.product_id, p.product_name" in sql
    assert "WHERE ranking <= :top_n" in sql and "LIMIT" not in sql.upper()
    assert query.parameters == {"top_n": 5}
    assert query.source_tables == ("gold.fct_sales", "gold.dim_products")


def test_top_products_by_quantity():
    query = compile_(_req(Metric.TOTAL_QUANTITY_SOLD, Operation.RANKING, [Dimension.PRODUCT]))
    sql = _norm(query.statement)

    assert "SUM(s.order_item_quantity) AS quantity_sold" in sql
    assert "ORDER BY quantity_sold DESC" in sql
    assert query.parameters == {"top_n": 10}  # default; a bound parameter, never interpolated


def test_revenue_by_category():
    query = compile_(_req(Metric.NET_REVENUE, Operation.BREAKDOWN, [Dimension.CATEGORY]))

    assert query.statement == (
        "SELECT\n"
        "    p.category_name,\n"
        "    SUM(s.order_item_net_revenue_after_returns) AS net_revenue\n"
        "FROM test_catalog.gold.fct_sales AS s\n"
        "INNER JOIN test_catalog.gold.dim_products AS p\n"
        "    ON s.product_variant_id = p.product_variant_id\n"
        "WHERE s.is_effective_sale = TRUE\n"
        "GROUP BY\n"
        "    p.category_name\n"
        "ORDER BY\n"
        "    net_revenue DESC,\n"
        "    category_name\n"
    )
    assert query.parameters == {}


def test_a_date_filtered_query_uses_bound_dates():
    query = compile_(_req(Metric.NET_REVENUE, filters=RequestFilters(date_range=YEAR_2025)))

    assert query.statement == (
        "SELECT\n"
        "    SUM(s.order_item_net_revenue_after_returns) AS net_revenue\n"
        "FROM test_catalog.gold.fct_sales AS s\n"
        "WHERE s.is_effective_sale = TRUE\n"
        "    AND s.order_date >= CAST(:date_from AS DATE)\n"
        "    AND s.order_date <= CAST(:date_to AS DATE)\n"
    )
    assert query.parameters == {"date_from": "2025-01-01", "date_to": "2025-12-31"}
    assert "2025" not in query.statement  # the dates are parameters, not text
    assert query.applied_filters["period"] == "2025-01-01..2025-12-31"


@pytest.mark.parametrize("grain, expected", [
    (TimeGrain.MONTH, "date_trunc('MONTH', s.order_date)"),
    (TimeGrain.QUARTER, "date_trunc('QUARTER', s.order_date)"),
    (TimeGrain.YEAR, "date_trunc('YEAR', s.order_date)"),
])
def test_trend_groups_by_the_time_grain(grain, expected):
    sql = _norm(compile_(_req(Metric.NET_REVENUE, Operation.TREND, time_grain=grain)).statement)

    assert f"{expected} AS period" in sql and f"GROUP BY {expected}" in sql and sql.endswith("ORDER BY period")


def test_a_daily_trend_needs_and_uses_a_bounded_period():
    request = _req(
        Metric.NET_REVENUE, Operation.TREND, time_grain=TimeGrain.DAY, filters=RequestFilters(date_range=YEAR_2025)
    )
    sql = _norm(compile_(request).statement)

    assert "s.order_date AS period" in sql and "GROUP BY s.order_date" in sql


# ---------------------------------------------------------------------------
# business semantics
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("metric", [m for m, d in METRICS.items() if d.fact == "sales"])
def test_every_sales_metric_keeps_the_effective_sale_filter(metric):
    definition = METRICS[metric]
    dimensions = [Dimension.LOCATION]
    request = _req(metric, Operation.BREAKDOWN, dimensions)
    assert "s.is_effective_sale = TRUE" in compile_(request).statement


def test_average_ticket_is_computed_per_order_not_per_item():
    sql = compile_(_req(Metric.AVERAGE_TICKET)).statement

    assert "MAX(s.order_total_amount) AS order_total" in sql  # order_total_amount repeats on every item
    assert "GROUP BY\n        s.order_id" in sql
    assert "SUM(order_total) / NULLIF(COUNT(*), 0) AS average_ticket" in sql
    assert "SUM(s.order_total_amount)" not in sql  # never summed at item grain


def test_average_ticket_by_store_groups_orders_by_order_level_attributes():
    sql = _norm(compile_(_req(Metric.AVERAGE_TICKET, Operation.BREAKDOWN, [Dimension.LOCATION])).statement)

    assert "LEFT JOIN test_catalog.gold.dim_locations AS l ON s.location_id = l.location_id" in sql
    assert "GROUP BY s.order_id, l.location_name" in sql
    assert "FROM orders GROUP BY location_name" in sql


def test_average_ticket_cannot_be_compiled_by_product_or_category():
    for dimension in (Dimension.PRODUCT, Dimension.CATEGORY):
        with pytest.raises(UnsupportedRequestError):
            compile_(_req(Metric.AVERAGE_TICKET, Operation.BREAKDOWN, [dimension]))


def test_return_rate_is_a_divide_with_visible_numerator_and_denominator():
    sql = _norm(compile_(_req(Metric.RETURN_RATE)).statement)

    assert "SUM(s.completed_returned_quantity) AS return_rate_numerator" in sql
    assert "SUM(s.order_item_quantity) AS return_rate_denominator" in sql
    assert "SUM(s.completed_returned_quantity) / NULLIF(SUM(s.order_item_quantity), 0) AS return_rate" in sql  # DIVIDE -> NULL, never an error


def test_snapshot_metrics_have_no_date_predicate_and_no_effective_sale_filter():
    for metric in (Metric.STOCK_ON_HAND, Metric.INVENTORY_ITEMS, Metric.STOCKOUT_RISK, Metric.CUSTOMER_COUNT):
        query = compile_(_req(metric))
        assert "order_date" not in query.statement and ":date" not in query.statement
        assert "is_effective_sale" not in query.statement
        assert query.applied_filters["period"] == "snapshot"


def test_at_risk_metrics_filter_the_at_risk_segment_like_power_bi():
    assert "WHERE k.customer_segment = 'at_risk'" in compile_(_req(Metric.AT_RISK_CUSTOMERS)).statement
    revenue = compile_(_req(Metric.AT_RISK_REVENUE)).statement
    assert "SUM(k.monetary_amount) AS at_risk_revenue" in revenue and "k.customer_segment = 'at_risk'" in revenue


def test_inventory_health_breakdown_uses_the_fixed_business_order():
    sql = _norm(compile_(_req(Metric.INVENTORY_ITEMS, Operation.BREAKDOWN, [Dimension.INVENTORY_HEALTH_STATUS])).statement)

    assert "ORDER BY CASE i.inventory_health_status WHEN 'out_of_stock' THEN 1 WHEN 'critical' THEN 2" in sql
    assert "WHEN 'no_recent_sales' THEN 5" in sql


def test_days_to_stockout_ranks_the_soonest_first_and_skips_nulls():
    sql = _norm(compile_(_req(Metric.DAYS_TO_STOCKOUT, Operation.RANKING, [Dimension.PRODUCT])).statement)

    assert "ORDER BY days_to_stockout ASC" in sql
    assert "WHERE days_to_stockout IS NOT NULL" in sql


def test_salesperson_ranking_uses_rank_that_skips_positions_like_default_rankx():
    sql = _norm(compile_(_req(Metric.NET_REVENUE, Operation.RANKING, [Dimension.SALESPERSON])).statement)
    assert " RANK() OVER (" in sql and "DENSE_RANK" not in sql


def test_salesperson_ranking_applies_the_filters_of_the_power_bi_ranking_visual():
    query = compile_(_req(Metric.NET_REVENUE, Operation.RANKING, [Dimension.SALESPERSON], top_n=3))
    sql = _norm(query.statement)

    assert "INNER JOIN test_catalog.gold.dim_employees AS e ON s.salesperson_id = e.employee_id" in sql
    assert "AND e.employee_role = 'Salesperson' AND e.employee_full_name IS NOT NULL" in sql
    assert "s.is_effective_sale = TRUE" in sql
    assert query.parameters == {"top_n": 3}


def test_the_salesperson_rule_travels_with_the_dimension_wherever_it_is_used():
    ticket = _norm(compile_(_req(Metric.AVERAGE_TICKET, Operation.RANKING, [Dimension.SALESPERSON])).statement)
    assert "e.employee_role = 'Salesperson'" in ticket and "e.employee_full_name IS NOT NULL" in ticket
    # ...and not where the dimension is not used
    assert "employee" not in compile_(_req(Metric.NET_REVENUE, Operation.RANKING, [Dimension.PRODUCT])).statement


def test_product_and_category_ranking_use_dense_rank():
    for dimension in (Dimension.PRODUCT, Dimension.CATEGORY):
        assert "DENSE_RANK() OVER (" in compile_(_req(Metric.NET_REVENUE, Operation.RANKING, [dimension])).statement


def test_state_dimensions_join_the_right_dimension_table():
    location = _norm(compile_(_req(Metric.NET_REVENUE, Operation.BREAKDOWN, [Dimension.LOCATION_STATE])).statement)
    customer = _norm(compile_(_req(Metric.NET_REVENUE, Operation.BREAKDOWN, [Dimension.CUSTOMER_STATE])).statement)

    assert "l.location_state" in location and "dim_locations" in location and "dim_customers" not in location
    assert "c.primary_address_state AS customer_state" in customer and "dim_customers" in customer and "dim_locations" not in customer


def test_a_category_filter_joins_products_and_binds_its_values():
    request = _req(Metric.NET_REVENUE, filters=RequestFilters(categories=("Coletes Salva-Vidas", "Motores")))
    query = compile_(request)

    assert "INNER JOIN test_catalog.gold.dim_products AS p" in query.statement
    assert "p.category_name IN (:category_0, :category_1)" in query.statement
    assert query.parameters == {"category_0": "Coletes Salva-Vidas", "category_1": "Motores"}
    assert "Coletes" not in query.statement and "Motores" not in query.statement


def test_the_join_to_a_dimension_is_added_once_even_if_used_twice():
    request = _req(
        Metric.NET_REVENUE, Operation.BREAKDOWN, [Dimension.CATEGORY], filters=RequestFilters(categories=("Motores",))
    )
    assert compile_(request).statement.count("dim_products AS p") == 1


# ---------------------------------------------------------------------------
# time modifiers (Power BI TOTALYTD / DATESINPERIOD / SAMEPERIODLASTYEAR)
# ---------------------------------------------------------------------------


MARCH_2025 = DateRange(date(2025, 3, 1), date(2025, 3, 31))


def test_year_to_date_is_anchored_on_the_end_of_the_given_period_like_totalytd():
    request = _req(Metric.NET_REVENUE, time_modifier=TimeModifier.YTD, filters=RequestFilters(date_range=MARCH_2025))
    query = compile_(request)

    assert query.statement == (
        "SELECT\n"
        "    SUM(s.order_item_net_revenue_after_returns) AS net_revenue\n"
        "FROM test_catalog.gold.fct_sales AS s\n"
        "WHERE s.is_effective_sale = TRUE\n"
        "    AND s.order_date >= trunc(CAST(:date_to AS DATE), 'YEAR')\n"
        "    AND s.order_date <= CAST(:date_to AS DATE)\n"
    )
    # Only the end of the period is used (TOTALYTD replaces the date filter with the year-to-date window).
    assert query.parameters == {"date_to": "2025-03-31"}
    assert query.applied_filters["period"] == "YTD@2025-03-31"


def test_last_twelve_months_window_matches_datesinperiod_on_the_end_of_the_period():
    request = _req(Metric.NET_REVENUE, time_modifier=TimeModifier.LTM, filters=RequestFilters(date_range=YEAR_2025))
    query = compile_(request)
    sql = _norm(query.statement)

    assert "s.order_date > add_months(CAST(:date_to AS DATE), -12) AND s.order_date <= CAST(:date_to AS DATE)" in sql
    assert "s.is_effective_sale = TRUE" in sql
    assert query.parameters == {"date_to": "2025-12-31"} and query.applied_filters["period"] == "LTM@2025-12-31"


@pytest.mark.parametrize("modifier", list(TimeModifier))
def test_no_time_modifier_is_ever_compiled_without_a_period(modifier):
    with pytest.raises(UnsupportedRequestError, match="explicit period"):
        compile_(_req(Metric.NET_REVENUE, time_modifier=modifier))


@pytest.mark.parametrize("modifier", list(TimeModifier))
def test_time_modifiers_never_derive_a_period_from_the_data(modifier):
    request = _req(Metric.NET_REVENUE, time_modifier=modifier, filters=RequestFilters(date_range=YEAR_2025))
    sql = compile_(request).statement

    assert "MAX(" not in sql and "current_date" not in sql.lower() and "now()" not in sql.lower()
    assert "CROSS JOIN" not in sql and "anchor" not in sql  # no anchor computed from fct_sales


def test_year_over_year_compares_with_the_same_period_of_the_previous_year():
    query = compile_(_req(Metric.NET_REVENUE, time_modifier=TimeModifier.YOY, filters=RequestFilters(date_range=YEAR_2025)))
    sql = _norm(query.statement)

    assert "add_months(CAST(:date_from AS DATE), -12)" in sql and "add_months(CAST(:date_to AS DATE), -12)" in sql
    assert "(current_value - previous_value) / NULLIF(previous_value, 0) AS net_revenue_yoy" in sql  # DIVIDE
    assert query.parameters == {"date_from": "2025-01-01", "date_to": "2025-12-31"}
    assert query.applied_filters["comparison"] == "same_period_previous_year"


# ---------------------------------------------------------------------------
# safety properties, over EVERY valid combination in the registry
# ---------------------------------------------------------------------------


def _all_valid_requests():
    yield from _enumerate_requests()


def _enumerate_requests():
    for metric, definition in METRICS.items():
        for operation in sorted(definition.operations, key=lambda o: o.value):
            if operation in (Operation.RANKING, Operation.BREAKDOWN):
                for dimension in sorted(definition.dimensions, key=lambda d: d.value):
                    if operation is Operation.BREAKDOWN and DIMENSIONS[dimension].high_cardinality:
                        continue
                    yield _req(metric, operation, [dimension])
            elif operation is Operation.TREND:
                if not definition.snapshot:
                    for grain in (TimeGrain.MONTH, TimeGrain.QUARTER, TimeGrain.YEAR):
                        yield _req(metric, operation, time_grain=grain)
            else:
                yield _req(metric, operation)
        for modifier in definition.time_modifiers:
            yield _req(metric, Operation.AGGREGATE, time_modifier=modifier, filters=RequestFilters(date_range=YEAR_2025))
        if FilterKind.DATE_RANGE in definition.filters:
            yield _req(metric, Operation.AGGREGATE, filters=RequestFilters(date_range=YEAR_2025)) if Operation.AGGREGATE in definition.operations else _req(
                metric, Operation.RANKING, [next(iter(sorted(definition.dimensions, key=lambda d: d.value)))], filters=RequestFilters(date_range=YEAR_2025)
            )


ALL_REQUESTS = list(_all_valid_requests())


def test_the_enumeration_covers_a_meaningful_share_of_the_registry():
    assert len(ALL_REQUESTS) > 150
    assert {r.metric for r in ALL_REQUESTS} == set(Metric)
    assert {r.operation for r in ALL_REQUESTS} == set(Operation)


@pytest.mark.parametrize("request_", ALL_REQUESTS, ids=lambda r: f"{r.operation.value}-{r.metric.value}-{'+'.join(d.value for d in r.dimensions) or (r.time_grain.value if r.time_grain else (r.time_modifier.value if r.time_modifier else 'plain'))}")
def test_every_valid_request_compiles_to_safe_sql(request_):
    query = compile_(request_)
    statement = query.statement

    # no leftovers of string formatting, no statement separators, no comments
    assert "{" not in statement and "}" not in statement and ";" not in statement and "--" not in statement
    # never LIMIT: a ranking keeps ties through `ranking <= :top_n`
    assert "LIMIT" not in statement.upper()
    # every parameter is bound by name, and only by name
    used = set(re.findall(r":([a-z_][a-z0-9_]*)", statement))
    assert used == set(query.parameters)
    # only allowlisted tables, always fully qualified with the configured catalog.schema
    tables = re.findall(r"(?:FROM|JOIN) ([\w.]+)", statement)
    referenced = {t.split(".")[-1] for t in tables if t.startswith(f"{CATALOG}.{SCHEMA}.")}
    assert referenced <= ALLOWED_TABLES
    assert all(t.startswith(f"{CATALOG}.{SCHEMA}.") or t in ("orders", "anchor", "periods", "grouped", "ranked") for t in tables)
    # result shape is fully described, and named in the SQL
    assert query.columns
    for column in query.columns:
        assert re.search(rf"\b{column.name}\b", statement), column.name
    assert query.request is request_


def test_every_valid_request_passes_full_validation():
    for request_ in ALL_REQUESTS:
        validate_request(request_)


# ---------------------------------------------------------------------------
# input can never become SQL
# ---------------------------------------------------------------------------


def test_filter_values_are_bound_never_interpolated():
    hostile = "x'); DROP TABLE fct_sales; --"
    query = compile_(_req(Metric.NET_REVENUE, filters=RequestFilters(categories=(hostile,))))

    assert hostile not in query.statement and "DROP" not in query.statement
    assert query.parameters["category_0"] == hostile


def test_hostile_filter_values_with_control_characters_are_rejected():
    with pytest.raises(InvalidParameterError):
        compile_(_req(Metric.NET_REVENUE, filters=RequestFilters(categories=("a\nb",))))


@pytest.mark.parametrize("value", ["cat; DROP", "a.b", "1abc", "", "a b", "a-b", None, 5])
def test_catalog_and_schema_must_be_plain_identifiers(value):
    request = _req(Metric.NET_REVENUE)
    with pytest.raises(InvalidParameterError):
        compile_request(request, catalog=value, schema=SCHEMA)
    with pytest.raises(InvalidParameterError):
        compile_request(request, catalog=CATALOG, schema=value)
    with pytest.raises(InvalidParameterError):
        validate_identifier(value, label="catalog")


def test_an_invalid_combination_is_never_compiled():
    with pytest.raises(UnsupportedRequestError):
        compile_(_req(Metric.AVERAGE_TICKET, Operation.BREAKDOWN, [Dimension.PRODUCT]))
    with pytest.raises(UnsupportedRequestError):
        compile_(_req(Metric.STOCK_ON_HAND, filters=RequestFilters(date_range=YEAR_2025)))
    with pytest.raises(InvalidParameterError):
        compile_(_req(Metric.NET_REVENUE, Operation.RANKING, [Dimension.PRODUCT], top_n=51))


def test_compiled_parameters_are_supported_by_the_existing_executor_as_is():
    request = _req(Metric.NET_REVENUE, Operation.RANKING, [Dimension.PRODUCT], top_n=5, filters=RequestFilters(date_range=YEAR_2025, categories=("Motores",)))
    query = compile_(request)

    typed = {name: _parameter(name, value) for name, value in query.parameters.items()}
    assert typed["top_n"].type == "INT" and typed["top_n"].value == "5"
    assert typed["date_from"].type == "STRING" and typed["date_from"].value == "2025-01-01"  # CAST(:date_from AS DATE) in the SQL
    assert typed["category_0"].type == "STRING"


def test_compilation_is_pure_and_deterministic():
    request = _req(Metric.NET_REVENUE, Operation.BREAKDOWN, [Dimension.CATEGORY], filters=RequestFilters(date_range=YEAR_2025))
    assert compile_(request) == compile_(request)


# ---------------------------------------------------------------------------
# Phase 6B.1 top products: same semantics through the new infrastructure
# ---------------------------------------------------------------------------


def _golden(catalog=CATALOG, schema=SCHEMA) -> str:
    # The only difference from the validated statement is the generic name of
    # the first CTE.
    return TOP_PRODUCTS_BY_QUANTITY_SQL.format(catalog=catalog, schema=schema).replace("product_sales", "grouped")


def test_the_legacy_intent_compiles_to_the_validated_6b0_statement():
    query = prepare_query(StructuredIntent.TOP_PRODUCTS_BY_QUANTITY, {"top_n": 10}, catalog=CATALOG, schema=SCHEMA)

    assert query.statement == _golden()
    assert query.parameters == {"top_n": 10}


def test_the_equivalent_analytical_request_compiles_to_the_same_statement():
    request = _req(Metric.TOTAL_QUANTITY_SOLD, Operation.RANKING, [Dimension.PRODUCT], top_n=10)
    assert compile_(request).statement == _golden()


def test_the_top_products_semantics_are_all_present():
    sql = compile_(_req(Metric.TOTAL_QUANTITY_SOLD, Operation.RANKING, [Dimension.PRODUCT])).statement

    assert "WHERE s.is_effective_sale = TRUE" in sql  # effective sales only
    assert "ON s.product_variant_id = p.product_variant_id" in sql  # join on the variant
    assert "GROUP BY\n        p.product_id,\n        p.product_name" in sql  # product grain
    assert "SUM(s.order_item_quantity) AS quantity_sold" in sql
    assert "DENSE_RANK() OVER (\n            ORDER BY quantity_sold DESC\n        ) AS ranking" in sql
    assert "WHERE ranking <= :top_n" in sql and "LIMIT" not in sql  # ranking positions, ties kept
    assert "WHERE p." not in sql  # the whole product catalogue: no category/period restriction


def test_the_phase_6b0_rank_8_tie_remains_representable():
    # Two products tied at rank 8 must both come back for top_n = 10: the
    # statement filters on the rank, so it can return more rows than top_n.
    query = prepare_query(StructuredIntent.TOP_PRODUCTS_BY_QUANTITY, {"top_n": 10}, catalog=CATALOG, schema=SCHEMA)

    assert "ranking <= :top_n" in query.statement and "LIMIT" not in query.statement
    ranking = next(c for c in query.columns if c.name == "ranking")
    assert ranking.type.value == "INT"
    assert query.applied_filters == {"population": "effective_sales", "period": "all", "location": "all", "top_n": 10}


def test_prepare_query_still_rejects_unknown_intents_and_parameters():
    with pytest.raises(Exception, match="unsupported structured intent"):
        prepare_query("DROP TABLE", {}, catalog=CATALOG, schema=SCHEMA)
    with pytest.raises(InvalidParameterError, match="unknown parameter"):
        prepare_query(StructuredIntent.TOP_PRODUCTS_BY_QUANTITY, {"top_n": 5, "sql": "x"}, catalog=CATALOG, schema=SCHEMA)
    with pytest.raises(InvalidParameterError, match="unknown parameter"):
        prepare_query(StructuredIntent.ANALYTICS, {"request": _req(Metric.NET_REVENUE), "sql": "x"}, catalog=CATALOG, schema=SCHEMA)
    with pytest.raises(InvalidParameterError, match="AnalyticalRequest"):
        prepare_query(StructuredIntent.ANALYTICS, {"request": "SELECT 1"}, catalog=CATALOG, schema=SCHEMA)
