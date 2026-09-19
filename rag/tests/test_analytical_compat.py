"""Compatibility-matrix tests: invalid combinations fail closed.

``check_compatibility`` / ``validate_request`` decide, from the registry,
whether an AnalyticalRequest is allowed. Nothing is compiled or executed
here — these are pure validation tests.
"""

from __future__ import annotations

import dataclasses
from datetime import date

import pytest

from src.structured.analytical_request import (
    DEFAULT_TOP_N,
    MAX_DAILY_TREND_DAYS,
    MAX_TOP_N,
    AnalyticalRequest,
    DateRange,
    RequestFilters,
    check_compatibility,
    validate_request,
)
from src.structured.models import InvalidParameterError, UnsupportedRequestError
from src.structured.semantic_model import (
    DIMENSIONS,
    METRICS,
    Dimension,
    Metric,
    Operation,
    TimeGrain,
    TimeModifier,
)

YEAR_2025 = DateRange(date(2025, 1, 1), date(2025, 12, 31))


def _req(metric, operation=Operation.AGGREGATE, dimensions=(), **kwargs) -> AnalyticalRequest:
    return AnalyticalRequest(metric=metric, operation=operation, dimensions=tuple(dimensions), **kwargs)


# ---------------------------------------------------------------------------
# requests that must be accepted
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "request_",
    [
        _req(Metric.NET_REVENUE),
        _req(Metric.NET_REVENUE, filters=RequestFilters(date_range=YEAR_2025)),
        _req(Metric.NET_REVENUE, Operation.RANKING, [Dimension.PRODUCT], top_n=1),
        _req(Metric.TOTAL_QUANTITY_SOLD, Operation.RANKING, [Dimension.PRODUCT]),
        _req(Metric.NET_REVENUE, Operation.BREAKDOWN, [Dimension.CATEGORY]),
        _req(Metric.NET_REVENUE, Operation.BREAKDOWN, [Dimension.LOCATION_STATE]),
        _req(Metric.NET_REVENUE, Operation.BREAKDOWN, [Dimension.CUSTOMER_STATE]),
        _req(Metric.NET_REVENUE, Operation.TREND, time_grain=TimeGrain.MONTH),
        _req(Metric.NET_REVENUE, time_modifier=TimeModifier.YTD, filters=RequestFilters(date_range=YEAR_2025)),
        _req(Metric.NET_REVENUE, time_modifier=TimeModifier.LTM, filters=RequestFilters(date_range=YEAR_2025)),
        _req(Metric.NET_REVENUE, time_modifier=TimeModifier.YOY, filters=RequestFilters(date_range=YEAR_2025)),
        _req(Metric.AVERAGE_TICKET, Operation.BREAKDOWN, [Dimension.LOCATION]),
        _req(Metric.AVERAGE_TICKET, Operation.TREND, time_grain=TimeGrain.QUARTER),
        _req(Metric.RETURN_RATE, Operation.BREAKDOWN, [Dimension.CATEGORY]),
        _req(Metric.STOCKOUT_RISK),
        _req(Metric.INVENTORY_ITEMS, Operation.BREAKDOWN, [Dimension.INVENTORY_HEALTH_STATUS]),
        _req(Metric.AT_RISK_CUSTOMERS),
        _req(Metric.AT_RISK_CUSTOMERS, Operation.BREAKDOWN, [Dimension.CUSTOMER_STATE]),
        _req(Metric.DAYS_TO_STOCKOUT, Operation.RANKING, [Dimension.PRODUCT]),
    ],
)
def test_valid_requests_are_accepted(request_):
    check_compatibility(request_)
    assert validate_request(request_) is request_


# ---------------------------------------------------------------------------
# average ticket is order-level
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("dimension", [Dimension.PRODUCT, Dimension.CATEGORY])
@pytest.mark.parametrize("operation", [Operation.BREAKDOWN, Operation.RANKING])
def test_average_ticket_must_not_be_grouped_by_item_level_dimensions(operation, dimension):
    with pytest.raises(UnsupportedRequestError, match="AVERAGE_TICKET cannot be broken down by"):
        check_compatibility(_req(Metric.AVERAGE_TICKET, operation, [dimension]))


def test_average_ticket_does_not_accept_a_category_filter():
    with pytest.raises(UnsupportedRequestError, match="CATEGORY filter"):
        check_compatibility(_req(Metric.AVERAGE_TICKET, filters=RequestFilters(categories=("Coletes",))))


# ---------------------------------------------------------------------------
# snapshot metrics have no time
# ---------------------------------------------------------------------------

SNAPSHOT_METRICS = [m for m, d in METRICS.items() if d.snapshot]


def _valid_snapshot_request(metric, **kwargs) -> AnalyticalRequest:
    """A structurally valid request for the metric (so a refusal can only be
    about the time capability that is added on top)."""
    if Operation.AGGREGATE in METRICS[metric].operations:
        return _req(metric, **kwargs)
    return _req(metric, Operation.RANKING, [Dimension.PRODUCT], **kwargs)


def test_there_are_inventory_and_customer_snapshot_metrics():
    assert {Metric.STOCK_ON_HAND, Metric.INVENTORY_ITEMS, Metric.AT_RISK_CUSTOMERS, Metric.CUSTOMER_COUNT} <= set(SNAPSHOT_METRICS)


@pytest.mark.parametrize("metric", SNAPSHOT_METRICS)
def test_snapshot_metrics_reject_a_date_filter(metric):
    with pytest.raises(UnsupportedRequestError, match="does not accept the DATE_RANGE filter|current snapshot"):
        check_compatibility(_valid_snapshot_request(metric, filters=RequestFilters(date_range=YEAR_2025)))


@pytest.mark.parametrize("metric", SNAPSHOT_METRICS)
def test_snapshot_metrics_reject_a_trend(metric):
    with pytest.raises(UnsupportedRequestError):
        check_compatibility(_req(metric, Operation.TREND, time_grain=TimeGrain.MONTH))


@pytest.mark.parametrize("metric", SNAPSHOT_METRICS)
@pytest.mark.parametrize("modifier", list(TimeModifier))
def test_snapshot_metrics_reject_time_modifiers(metric, modifier):
    with pytest.raises(UnsupportedRequestError, match="current snapshot|has no"):
        check_compatibility(_valid_snapshot_request(metric, time_modifier=modifier))


# ---------------------------------------------------------------------------
# operation / dimension / filter structure
# ---------------------------------------------------------------------------


def test_a_dimension_the_metric_does_not_have_is_refused():
    with pytest.raises(UnsupportedRequestError, match="cannot be broken down by"):
        check_compatibility(_req(Metric.AT_RISK_CUSTOMERS, Operation.BREAKDOWN, [Dimension.CATEGORY]))
    with pytest.raises(UnsupportedRequestError, match="cannot be broken down by"):
        check_compatibility(_req(Metric.STOCK_ON_HAND, Operation.BREAKDOWN, [Dimension.CHANNEL]))


def test_an_operation_the_metric_does_not_support_is_refused():
    with pytest.raises(UnsupportedRequestError, match="does not support RANKING"):
        check_compatibility(_req(Metric.CUSTOMER_COUNT, Operation.RANKING, [Dimension.CUSTOMER_SEGMENT]))
    with pytest.raises(UnsupportedRequestError, match="does not support AGGREGATE"):
        # a sum of days across items is not a meaningful single total
        check_compatibility(_req(Metric.DAYS_TO_STOCKOUT))


def test_ranking_and_breakdown_need_exactly_one_dimension():
    for operation in (Operation.RANKING, Operation.BREAKDOWN):
        with pytest.raises(UnsupportedRequestError, match="exactly one dimension"):
            check_compatibility(_req(Metric.NET_REVENUE, operation, []))
        with pytest.raises(UnsupportedRequestError, match="exactly one dimension"):
            check_compatibility(_req(Metric.NET_REVENUE, operation, [Dimension.CATEGORY, Dimension.LOCATION]))


def test_aggregate_and_trend_take_no_dimension():
    with pytest.raises(UnsupportedRequestError, match="does not take a dimension"):
        check_compatibility(_req(Metric.NET_REVENUE, Operation.AGGREGATE, [Dimension.CATEGORY]))
    with pytest.raises(UnsupportedRequestError, match="does not take a dimension"):
        check_compatibility(_req(Metric.NET_REVENUE, Operation.TREND, [Dimension.CATEGORY], time_grain=TimeGrain.MONTH))


@pytest.mark.parametrize("dimension", [d for d, definition in DIMENSIONS.items() if definition.high_cardinality])
def test_high_cardinality_dimensions_can_only_be_ranked_never_listed(dimension):
    with pytest.raises(UnsupportedRequestError, match="too many values"):
        check_compatibility(_req(Metric.NET_REVENUE, Operation.BREAKDOWN, [dimension]))
    check_compatibility(_req(Metric.NET_REVENUE, Operation.RANKING, [dimension]))


def test_top_n_is_only_valid_for_ranking():
    with pytest.raises(UnsupportedRequestError, match="top_n"):
        check_compatibility(_req(Metric.NET_REVENUE, top_n=5))


def test_a_time_grain_is_only_valid_for_trend_and_trend_needs_one():
    with pytest.raises(UnsupportedRequestError, match="time grain"):
        check_compatibility(_req(Metric.NET_REVENUE, Operation.TREND))
    with pytest.raises(UnsupportedRequestError, match="only valid for TREND"):
        check_compatibility(_req(Metric.NET_REVENUE, time_grain=TimeGrain.MONTH))


def test_filters_outside_the_metric_are_refused():
    with pytest.raises(UnsupportedRequestError, match="INVENTORY_HEALTH_STATUS filter"):
        check_compatibility(_req(Metric.NET_REVENUE, filters=RequestFilters(inventory_health_statuses=("critical",))))
    with pytest.raises(UnsupportedRequestError, match="CUSTOMER_SEGMENT filter"):
        check_compatibility(_req(Metric.AT_RISK_CUSTOMERS, filters=RequestFilters(customer_segments=("loyal",))))


# ---------------------------------------------------------------------------
# time modifiers
# ---------------------------------------------------------------------------


def test_time_modifiers_only_exist_for_net_revenue():
    for metric in (Metric.TOTAL_QUANTITY_SOLD, Metric.EFFECTIVE_ORDERS, Metric.AVERAGE_TICKET, Metric.RETURN_RATE):
        with pytest.raises(UnsupportedRequestError, match="has no YTD variant"):
            check_compatibility(_req(metric, time_modifier=TimeModifier.YTD))


def test_time_modifiers_are_only_a_single_aggregate_value():
    with pytest.raises(UnsupportedRequestError, match="only supported as a single aggregate"):
        check_compatibility(_req(Metric.NET_REVENUE, Operation.BREAKDOWN, [Dimension.CATEGORY], time_modifier=TimeModifier.LTM))
    with pytest.raises(UnsupportedRequestError):
        check_compatibility(_req(Metric.NET_REVENUE, Operation.TREND, time_grain=TimeGrain.MONTH, time_modifier=TimeModifier.YTD))


@pytest.mark.parametrize("modifier", list(TimeModifier))
def test_every_time_modifier_needs_an_explicit_period_and_none_is_assumed(modifier):
    # Power BI evaluates YTD / LTM / %YoY against the report's date context
    # (LTM and %YoY are BLANK without one). The Harness has no such context,
    # so without a period the request is refused instead of being completed.
    with pytest.raises(UnsupportedRequestError, match="needs an explicit period.*no period is assumed"):
        check_compatibility(_req(Metric.NET_REVENUE, time_modifier=modifier))
    with pytest.raises(UnsupportedRequestError, match="needs an explicit period"):
        validate_request(_req(Metric.NET_REVENUE, time_modifier=modifier, filters=RequestFilters(categories=("Motores",))))


@pytest.mark.parametrize("modifier", list(TimeModifier))
def test_every_time_modifier_is_accepted_with_a_period(modifier):
    validate_request(_req(Metric.NET_REVENUE, time_modifier=modifier, filters=RequestFilters(date_range=YEAR_2025)))


# ---------------------------------------------------------------------------
# "menos vendidos" and personal data are not representable at all
# ---------------------------------------------------------------------------


def test_the_request_has_no_way_to_ask_for_an_ascending_ranking():
    fields = {f.name for f in dataclasses.fields(AnalyticalRequest)}
    assert fields == {"metric", "operation", "dimensions", "filters", "time_grain", "time_modifier", "top_n"}
    assert not {"order", "direction", "sort", "sql", "where", "table", "column"} & fields


def test_personal_data_cannot_be_requested_through_any_registry_enum():
    names = " ".join([m.value for m in Metric] + [d.value for d in Dimension]).lower()
    assert "email" not in names and "phone" not in names


def test_requests_only_accept_approved_enum_values():
    with pytest.raises(UnsupportedRequestError):
        check_compatibility(AnalyticalRequest(metric="NET_REVENUE", operation=Operation.AGGREGATE))  # type: ignore[arg-type]
    with pytest.raises(UnsupportedRequestError):
        check_compatibility(_req(Metric.NET_REVENUE, Operation.BREAKDOWN, ["fct_sales.customer_email"]))  # type: ignore[list-item]


# ---------------------------------------------------------------------------
# bounds and literal values (validate_request)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("top_n", [0, -1, MAX_TOP_N + 1, 10_000])
def test_top_n_is_bounded(top_n):
    with pytest.raises(InvalidParameterError, match="between 1 and"):
        validate_request(_req(Metric.NET_REVENUE, Operation.RANKING, [Dimension.PRODUCT], top_n=top_n))


@pytest.mark.parametrize("top_n", [True, 2.5, "5"])
def test_top_n_must_be_a_real_integer(top_n):
    with pytest.raises(InvalidParameterError, match="integer"):
        validate_request(_req(Metric.NET_REVENUE, Operation.RANKING, [Dimension.PRODUCT], top_n=top_n))


def test_ranking_defaults_and_maxima_are_the_documented_ones():
    assert (DEFAULT_TOP_N, MAX_TOP_N) == (10, 50)
    validate_request(_req(Metric.NET_REVENUE, Operation.RANKING, [Dimension.PRODUCT]))  # default
    validate_request(_req(Metric.NET_REVENUE, Operation.RANKING, [Dimension.PRODUCT], top_n=MAX_TOP_N))


def test_a_date_range_must_be_ordered_and_a_daily_trend_bounded():
    backwards = RequestFilters(date_range=DateRange(date(2025, 6, 1), date(2025, 1, 1)))
    with pytest.raises(InvalidParameterError, match="must not be after"):
        validate_request(_req(Metric.NET_REVENUE, filters=backwards))

    with pytest.raises(InvalidParameterError, match="daily trend needs a period"):
        validate_request(_req(Metric.NET_REVENUE, Operation.TREND, time_grain=TimeGrain.DAY))

    too_long = RequestFilters(date_range=DateRange(date(2024, 1, 1), date(2025, 12, 31)))
    with pytest.raises(InvalidParameterError, match=str(MAX_DAILY_TREND_DAYS)):
        validate_request(_req(Metric.NET_REVENUE, Operation.TREND, time_grain=TimeGrain.DAY, filters=too_long))

    one_year = RequestFilters(date_range=DateRange(date(2025, 1, 1), date(2025, 12, 31)))
    validate_request(_req(Metric.NET_REVENUE, Operation.TREND, time_grain=TimeGrain.DAY, filters=one_year))


@pytest.mark.parametrize("value", ["", "   ", "x" * 81, "bad\nvalue"])
def test_filter_values_are_validated(value):
    with pytest.raises(InvalidParameterError, match="invalid category filter value"):
        validate_request(_req(Metric.NET_REVENUE, filters=RequestFilters(categories=(value,))))


def test_enumerated_filters_only_accept_known_values():
    with pytest.raises(InvalidParameterError, match="one of"):
        validate_request(_req(Metric.INVENTORY_ITEMS, filters=RequestFilters(inventory_health_statuses=("exploding",))))
    with pytest.raises(InvalidParameterError, match="one of"):
        validate_request(_req(Metric.CUSTOMER_COUNT, filters=RequestFilters(customer_segments=("vip",))))
    validate_request(_req(Metric.INVENTORY_ITEMS, filters=RequestFilters(inventory_health_statuses=("critical", "out_of_stock"))))
