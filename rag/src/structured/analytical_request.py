"""The typed semantic request and its compatibility matrix.

An :class:`AnalyticalRequest` is the only thing between natural language and
SQL. It is made of enums from the registry (an approved metric, operation,
dimensions, filters, time grain/modifier) plus a bounded ``top_n`` and
literal filter values — no SQL text, no table or column names. Anything
that is not representable here cannot be asked of the warehouse.

:func:`check_compatibility` decides, from data on the registry, whether the
combination is allowed; the rules are not scattered ``if`` statements per
metric:

- an operation must be in the metric's ``operations``;
- a dimension must be in the metric's ``dimensions`` (order-level metrics
  therefore reject item-level dimensions; ambiguity such as "state" is
  resolved by having two distinct dimensions, ``LOCATION_STATE`` and
  ``CUSTOMER_STATE``);
- a filter must be in the metric's ``filters``; snapshot metrics have no
  date filter, trend or time modifier;
- high-cardinality dimensions can only be ranked, never listed;
- YTD, LTM and YOY are evaluated against an explicit period (the end of the
  period is the anchor); without one the request is refused, never
  completed with an assumed period;
- there is deliberately no ascending ranking ("menos vendidos").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from src.structured.models import InvalidParameterError, UnsupportedRequestError
from src.structured.semantic_model import (
    CUSTOMER_SEGMENTS,
    DIMENSIONS,
    INVENTORY_HEALTH_STATUSES,
    METRICS,
    Dimension,
    FilterKind,
    Metric,
    Operation,
    TimeGrain,
    TimeModifier,
)

__all__ = [
    "DEFAULT_TOP_N",
    "MAX_TOP_N",
    "MAX_DAILY_TREND_DAYS",
    "DateRange",
    "RequestFilters",
    "AnalyticalRequest",
    "check_compatibility",
    "validate_request",
]

DEFAULT_TOP_N = 10
MAX_TOP_N = 50
#: A daily trend needs a bounded window so a result never has to be truncated.
MAX_DAILY_TREND_DAYS = 366
_MAX_VALUE_LENGTH = 80


@dataclass(frozen=True)
class DateRange:
    """Inclusive date interval on the fact's business date (sales:
    ``order_date``, which Power BI relates to ``dim_dates[date_day]``)."""

    start: date
    end: date


@dataclass(frozen=True)
class RequestFilters:
    date_range: DateRange | None = None
    categories: tuple[str, ...] = ()
    customer_segments: tuple[str, ...] = ()
    inventory_health_statuses: tuple[str, ...] = ()

    def present(self) -> frozenset[FilterKind]:
        present = set()
        if self.date_range is not None:
            present.add(FilterKind.DATE_RANGE)
        if self.categories:
            present.add(FilterKind.CATEGORY)
        if self.customer_segments:
            present.add(FilterKind.CUSTOMER_SEGMENT)
        if self.inventory_health_statuses:
            present.add(FilterKind.INVENTORY_HEALTH_STATUS)
        return frozenset(present)


@dataclass(frozen=True)
class AnalyticalRequest:
    metric: Metric
    operation: Operation
    dimensions: tuple[Dimension, ...] = ()
    filters: RequestFilters = field(default_factory=RequestFilters)
    time_grain: TimeGrain | None = None
    time_modifier: TimeModifier | None = None
    #: Ranking positions (not rows: ties are kept). ``None`` = default.
    top_n: int | None = None


def _reject(message: str) -> UnsupportedRequestError:
    return UnsupportedRequestError(message)


def check_compatibility(request: AnalyticalRequest) -> None:
    """Raise :class:`UnsupportedRequestError` unless the combination of
    approved parts is allowed by the registry. Value *bounds* (``top_n``
    range, date sanity, literal values) are :func:`validate_request`'s job."""
    if not isinstance(request.metric, Metric) or not isinstance(request.operation, Operation):
        raise _reject("metric and operation must be approved enum values")
    definition = METRICS[request.metric]
    name = request.metric.value
    op = request.operation

    if op not in definition.operations:
        raise _reject(f"{name} does not support {op.value}")

    # Dimensions ------------------------------------------------------------
    dimensions = request.dimensions
    if len(set(dimensions)) != len(dimensions):
        raise _reject("duplicate dimensions")
    for dimension in dimensions:
        if not isinstance(dimension, Dimension):
            raise _reject("dimensions must be approved enum values")
        if dimension not in definition.dimensions:
            raise _reject(f"{name} cannot be broken down by {dimension.value}")
    if op in (Operation.RANKING, Operation.BREAKDOWN):
        if len(dimensions) != 1:
            raise _reject(f"{op.value} needs exactly one dimension")
        if op is Operation.BREAKDOWN and DIMENSIONS[dimensions[0]].high_cardinality:
            raise _reject(f"{dimensions[0].value} has too many values to list; ask for a ranking (top N) instead")
    elif dimensions:  # AGGREGATE / TREND
        raise _reject(f"{op.value} does not take a dimension")

    # Filters ----------------------------------------------------------------
    filters = request.filters
    for kind in filters.present():
        if kind not in definition.filters:
            raise _reject(f"{name} does not accept the {kind.value} filter")
    if definition.snapshot and (
        filters.date_range is not None or request.time_grain is not None or request.time_modifier is not None
    ):
        raise _reject(f"{name} is a current snapshot: it has no date filter, trend or time modifier")

    # Time --------------------------------------------------------------------
    if op is Operation.TREND:
        if request.time_grain is None:
            raise _reject("TREND needs a time grain")
        if definition.snapshot:
            raise _reject(f"{name} has no history to trend")
    elif request.time_grain is not None:
        raise _reject("a time grain is only valid for TREND")

    if request.time_modifier is not None:
        modifier = request.time_modifier
        if modifier not in definition.time_modifiers:
            raise _reject(f"{name} has no {modifier.value} variant")
        if op is not Operation.AGGREGATE:
            raise _reject(f"{modifier.value} is only supported as a single aggregate value")
        if filters.date_range is None:
            # Power BI evaluates these against the date context of the report
            # (LTM and %YoY return BLANK without one). The Harness has no such
            # context and never invents a period: no period, no answer.
            raise _reject(
                f"{modifier.value} needs an explicit period to anchor on (a period is required); "
                "no period is assumed"
            )

    # Ranking -----------------------------------------------------------------
    if op is not Operation.RANKING and request.top_n is not None:
        raise _reject("top_n is only valid for RANKING")


def _validate_values(values: tuple[str, ...], label: str, allowed: tuple[str, ...] | None) -> None:
    for value in values:
        if not isinstance(value, str) or not value.strip() or len(value) > _MAX_VALUE_LENGTH or not value.isprintable():
            raise InvalidParameterError(f"invalid {label} filter value: {value!r}")
        if allowed is not None and value not in allowed:
            raise InvalidParameterError(f"{label} must be one of {list(allowed)}, got {value!r}")


def validate_request(request: AnalyticalRequest) -> AnalyticalRequest:
    """Full validation: compatibility, then bounds and literal values.
    Returns the request unchanged, or raises a ``StructuredQueryError``."""
    check_compatibility(request)

    if request.operation is Operation.RANKING:
        top_n = DEFAULT_TOP_N if request.top_n is None else request.top_n
        if isinstance(top_n, bool) or not isinstance(top_n, int):
            raise InvalidParameterError(f"top_n must be an integer, got {type(top_n).__name__}")
        if top_n < 1 or top_n > MAX_TOP_N:
            raise InvalidParameterError(f"top_n must be between 1 and {MAX_TOP_N}, got {top_n}")

    date_range = request.filters.date_range
    if date_range is not None:
        if not isinstance(date_range.start, date) or not isinstance(date_range.end, date):
            raise InvalidParameterError("date range bounds must be dates")
        if date_range.start > date_range.end:
            raise InvalidParameterError("date range start must not be after its end")
        if request.time_grain is TimeGrain.DAY and (date_range.end - date_range.start).days + 1 > MAX_DAILY_TREND_DAYS:
            raise InvalidParameterError(f"a daily trend is limited to {MAX_DAILY_TREND_DAYS} days")
    if request.time_grain is TimeGrain.DAY and date_range is None:
        raise InvalidParameterError(f"a daily trend needs a period of at most {MAX_DAILY_TREND_DAYS} days")

    _validate_values(request.filters.categories, "category", None)
    _validate_values(request.filters.customer_segments, "customer segment", CUSTOMER_SEGMENTS)
    _validate_values(request.filters.inventory_health_statuses, "inventory health status", INVENTORY_HEALTH_STATUSES)
    return request
