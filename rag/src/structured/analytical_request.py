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
- there is deliberately no ascending ranking ("menos vendidos"), except for
  metrics that declare the opposite order (stock: "menos estoque");
- a name filter (product, category, location) is free text reduced to search
  terms by :mod:`src.structured.text_match` — only metrics that declare the
  filter accept it, so a qualifier is never silently ignored;
- a metric can require filters (a forecast is for one product in one store) and is
  refused without them;
- ``as_of`` asks for a position at the end of a past day and only exists for
  metrics with a history fact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from src.structured.models import InvalidParameterError, UnsupportedRequestError
from src.structured.semantic_model import (
    CUSTOMER_SEGMENTS,
    DIMENSIONS,
    FACTS,
    INVENTORY_HEALTH_STATUSES,
    METRICS,
    Dimension,
    FilterKind,
    Metric,
    Operation,
    SortOrder,
    TimeGrain,
    TimeModifier,
)
from src.structured.text_match import MAX_TEXT_LENGTH, search_terms

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
    """Inclusive date interval on the fact's business date (sales: the date
    of ``placed_at``, ``CAST(s.placed_at AS DATE)``)."""

    start: date
    end: date


@dataclass(frozen=True)
class RequestFilters:
    date_range: DateRange | None = None
    categories: tuple[str, ...] = ()
    customer_segments: tuple[str, ...] = ()
    inventory_health_statuses: tuple[str, ...] = ()
    #: Free-text name filters, as the user wrote them ("coletes", "Motor de
    #: Popa 6014", "Loja Leão"); matched through search terms, never as SQL.
    product_text: str | None = None
    category_text: str | None = None
    location_text: str | None = None

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
        if self.product_text is not None:
            present.add(FilterKind.PRODUCT_TEXT)
        if self.category_text is not None:
            present.add(FilterKind.CATEGORY_TEXT)
        if self.location_text is not None:
            present.add(FilterKind.LOCATION_TEXT)
        return frozenset(present)

    def text_filters(self) -> dict[FilterKind, str]:
        """The free-text filters that are set, by kind."""
        values = {
            FilterKind.PRODUCT_TEXT: self.product_text,
            FilterKind.CATEGORY_TEXT: self.category_text,
            FilterKind.LOCATION_TEXT: self.location_text,
        }
        return {kind: value for kind, value in values.items() if value is not None}


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
    #: The position at the END of this (past) day, for metrics with a history
    #: fact. ``None`` = the current position.
    as_of: date | None = None
    #: Ranking direction; ``None`` = the metric's own order.
    ranking_order: SortOrder | None = None
    #: Keep only the groups whose value is greater than zero.
    positive_only: bool = False


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

    missing = definition.required_filters - filters.present()
    if missing:
        raise _reject(f"{name} needs the {' and '.join(sorted(kind.value for kind in missing))} filter(s): it is never widened")

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

    # As-of position ------------------------------------------------------------
    if request.as_of is not None:
        if definition.historical_fact is None:
            raise _reject(f"{name} has no history: it can only be asked for its current position")
        if filters.date_range is not None or request.time_grain is not None or request.time_modifier is not None:
            raise _reject("an as-of position takes a single date, not a period, trend or time modifier")
        history = FACTS[definition.historical_fact]
        for dimension in dimensions:
            if dimension not in history.bindings:
                raise _reject(f"{name} cannot be broken down by {dimension.value} in a past position")

    # Ranking -----------------------------------------------------------------
    if op is not Operation.RANKING and request.top_n is not None:
        raise _reject("top_n is only valid for RANKING")
    if request.ranking_order is not None:
        if op is not Operation.RANKING:
            raise _reject("a ranking order is only valid for RANKING")
        if not isinstance(request.ranking_order, SortOrder):
            raise _reject("ranking order must be an approved enum value")
        if request.ranking_order is not definition.ranking_order and not definition.ranking_reverse_pt:
            raise _reject(f"{name} is only ranked {definition.ranking_order.value}; the opposite order is not supported")
    if request.positive_only:
        if not definition.positive_only:
            raise _reject(f"{name} cannot be restricted to positive values")
        if op is Operation.AGGREGATE:
            raise _reject("positive-only applies to groups (a breakdown or a ranking), not to a single value")


def _validate_values(values: tuple[str, ...], label: str, allowed: tuple[str, ...] | None) -> None:
    for value in values:
        if not isinstance(value, str) or not value.strip() or len(value) > _MAX_VALUE_LENGTH or not value.isprintable():
            raise InvalidParameterError(f"invalid {label} filter value: {value!r}")
        if allowed is not None and value not in allowed:
            raise InvalidParameterError(f"{label} must be one of {list(allowed)}, got {value!r}")


def _validate_text_filter(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_TEXT_LENGTH or not value.isprintable():
        raise InvalidParameterError(f"invalid {label} filter value: {value!r}")
    try:
        terms = search_terms(value)
    except ValueError as exc:
        raise InvalidParameterError(f"{label} filter: {exc}") from exc
    if not terms:
        raise InvalidParameterError(f"{label} filter has no searchable word: {value!r}")


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

    if request.as_of is not None:
        if isinstance(request.as_of, bool) or not isinstance(request.as_of, date):
            raise InvalidParameterError("as_of must be a date")
        if request.as_of > date.today():
            raise InvalidParameterError("an as-of position cannot be in the future")

    for kind, value in request.filters.text_filters().items():
        _validate_text_filter(value, kind.value)

    _validate_values(request.filters.categories, "category", None)
    _validate_values(request.filters.customer_segments, "customer segment", CUSTOMER_SEGMENTS)
    _validate_values(request.filters.inventory_health_statuses, "inventory health status", INVENTORY_HEALTH_STATUSES)
    return request
