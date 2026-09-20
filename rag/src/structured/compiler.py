"""Deterministic SQL compiler: ``AnalyticalRequest`` -> allowlisted SQL.

The single place where a request becomes executable SQL. It is pure (no I/O,
no execution) and it only *assembles* reviewed fragments from the registry
(:mod:`src.structured.semantic_model`):

- tables and columns come from the registry's bindings, never from input;
- every user-controlled value (dates, ``top_n``, filter values, the search
  terms of a name filter) is a named parameter (``:date_from``, ``:top_n``,
  ``:category_0``, ``:product_term_0_0``, ``:as_of``, ...), never
  interpolated into the statement;
- catalog/schema are validated as plain identifiers;
- the fact's business filters (Power BI's ``is_effective_sale = TRUE()``
  for sales) are always applied;
- a ranking filters on ``ranking <= :top_n`` (never ``LIMIT``), so ties at
  the last position are kept.

The ranking layout intentionally reproduces the statement validated against
the real Gold layer in Phase 6B.0 (see ``queries.TOP_PRODUCTS_BY_QUANTITY_SQL``
and the regression test that compares them).
"""

from __future__ import annotations

import re
from typing import Any

from src.structured.analytical_request import (
    DEFAULT_TOP_N,
    AnalyticalRequest,
    validate_request,
)
from src.structured.models import (
    ColumnRole,
    ColumnSpec,
    ColumnType,
    InvalidParameterError,
    PreparedQuery,
    StructuredIntent,
)
from src.structured.text_match import SQL_ACCENTS_FROM, SQL_ACCENTS_TO, search_terms
from src.structured.semantic_model import (
    DIMENSIONS,
    FACTS,
    METRICS,
    Dimension,
    TEXT_FILTERS,
    FactDef,
    FilterKind,
    GroupColumn,
    JoinSpec,
    MetricDef,
    Operation,
    SortOrder,
    TimeGrain,
    TimeModifier,
    Unit,
)

__all__ = ["compile_request", "validate_identifier"]

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_GRAIN_SQL = {
    TimeGrain.MONTH: "date_trunc('MONTH', {col})",
    TimeGrain.QUARTER: "date_trunc('QUARTER', {col})",
    TimeGrain.YEAR: "date_trunc('YEAR', {col})",
    TimeGrain.DAY: "{col}",
}

_FILTER_DIMENSION = {
    FilterKind.CATEGORY: Dimension.CATEGORY,
    FilterKind.CUSTOMER_SEGMENT: Dimension.CUSTOMER_SEGMENT,
    FilterKind.INVENTORY_HEALTH_STATUS: Dimension.INVENTORY_HEALTH_STATUS,
}


def validate_identifier(value: object, *, label: str) -> str:
    """Catalog/schema names must be plain identifiers. They come from
    configuration, but are still validated so a misconfigured value can
    never smuggle SQL into a fixed statement."""
    if not isinstance(value, str) or not _IDENTIFIER_PATTERN.match(value):
        raise InvalidParameterError(f"{label} must be a plain identifier ([A-Za-z_][A-Za-z0-9_]*), got {value!r}")
    return value


# ---------------------------------------------------------------------------
# small text helpers
# ---------------------------------------------------------------------------


def _indent(lines: list[str], levels: int = 1) -> list[str]:
    pad = "    " * levels
    return [pad + line if line else line for line in lines]


def _comma(items: list[str]) -> list[str]:
    return [item + "," for item in items[:-1]] + items[-1:]


def _select_item(column: GroupColumn) -> str:
    return column.expr if column.expr.split(".")[-1] == column.name else f"{column.expr} AS {column.name}"


def _numeric_type(unit: Unit) -> ColumnType:
    return ColumnType.INT if unit in (Unit.UNITS, Unit.INTEGER) else ColumnType.DECIMAL


def _sum_inner(aggregate: str) -> str:
    assert aggregate.startswith("SUM(") and aggregate.endswith(")"), aggregate
    return aggregate[4:-1]


# ---------------------------------------------------------------------------
# compilation
# ---------------------------------------------------------------------------


class _Builder:
    def __init__(self, request: AnalyticalRequest, catalog: str, schema: str) -> None:
        self.request = request
        self.catalog = catalog
        self.schema = schema
        self.metric: MetricDef = METRICS[request.metric]
        # A position AS OF a date is read from the metric's history fact.
        self.fact: FactDef = FACTS[self.metric.historical_fact if request.as_of is not None else self.metric.fact]
        # Where the fact's table lives: its own catalog/schema when the registry says so
        # (validated like the App's), otherwise the App's DATA_CATALOG / DATA_SCHEMA.
        self.fact_catalog = validate_identifier(self.fact.catalog, label="fact catalog") if self.fact.catalog else catalog
        self.fact_schema = validate_identifier(self.fact.schema, label="fact schema") if self.fact.schema else schema
        self.parameters: dict[str, Any] = {}

    # -- names -----------------------------------------------------------
    def table(self, name: str) -> str:
        return f"{self.catalog}.{self.schema}.{name}"

    def fact_table(self) -> str:
        return f"{self.fact_catalog}.{self.fact_schema}.{self.fact.table}"

    # -- joins / columns ---------------------------------------------------
    def _needed_dimensions(self) -> list[Dimension]:
        needed = list(self.request.dimensions)
        filters = self.request.filters
        for kind in (FilterKind.CATEGORY, FilterKind.CUSTOMER_SEGMENT, FilterKind.INVENTORY_HEALTH_STATUS):
            if kind in filters.present():
                needed.append(_FILTER_DIMENSION[kind])
        needed.extend(
            TEXT_FILTERS[kind].dimension for kind in filters.text_filters() if kind not in self.fact.text_columns
        )
        return needed

    def joins(self) -> list[JoinSpec]:
        joins: list[JoinSpec] = []
        for dimension in self._needed_dimensions():
            join = self.fact.bindings[dimension].join
            if join is not None and all(existing.alias != join.alias for existing in joins):
                joins.append(join)
        return joins

    def group_columns(self) -> list[GroupColumn]:
        columns: list[GroupColumn] = []
        for dimension in self.request.dimensions:
            columns.extend(self.fact.bindings[dimension].columns)
        return columns

    def period_expr(self) -> str | None:
        if self.request.operation is not Operation.TREND:
            return None
        return _GRAIN_SQL[self.request.time_grain].format(col=self.fact.date_column)

    # -- filters ----------------------------------------------------------
    def predicates(self, *, include_date: bool = True) -> list[str]:
        predicates = list(self.fact.base_predicates) + list(self.metric.metric_predicates)
        if self.request.as_of is not None:
            predicates.extend(self.fact.as_of_predicates)
            self.parameters["as_of"] = self.request.as_of.isoformat()
        for dimension in self.request.dimensions:  # rules that always come with a dimension
            predicates.extend(self.fact.bindings[dimension].predicates)
        filters = self.request.filters
        if include_date and filters.date_range is not None:
            predicates.append(f"{self.fact.date_column} >= CAST(:date_from AS DATE)")
            predicates.append(f"{self.fact.date_column} <= CAST(:date_to AS DATE)")
            self.parameters["date_from"] = filters.date_range.start.isoformat()
            self.parameters["date_to"] = filters.date_range.end.isoformat()
        for kind, values, prefix in (
            (FilterKind.CATEGORY, filters.categories, "category"),
            (FilterKind.CUSTOMER_SEGMENT, filters.customer_segments, "segment"),
            (FilterKind.INVENTORY_HEALTH_STATUS, filters.inventory_health_statuses, "health"),
        ):
            if not values:
                continue
            expr = self.fact.bindings[_FILTER_DIMENSION[kind]].columns[0].expr
            names = []
            for index, value in enumerate(values):
                key = f"{prefix}_{index}"
                self.parameters[key] = value
                names.append(f":{key}")
            predicates.append(f"{expr} IN ({', '.join(names)})")
        if self.fact.official_window is not None:
            # Name filters and eligibility live in the CTEs (see ``official_window_ctes``).
            predicates.extend(self._official_window_predicates())
            return predicates
        predicates.extend(self._text_predicates())
        return predicates

    def _text_predicates(self) -> list[str]:
        """One predicate per search term of every name filter: the term must
        appear in the accent/case-folded column (any of its singular/plural
        variants). Terms are letters and digits only and travel as parameters
        (``:product_term_0_0`` = ``%colete%``)."""
        predicates: list[str] = []
        for kind, value in self.request.filters.text_filters().items():
            definition = TEXT_FILTERS[kind]
            column = self.fact.text_columns.get(kind, definition.expr)
            folded = f"translate(lower({column}), '{SQL_ACCENTS_FROM}', '{SQL_ACCENTS_TO}')"
            for term_index, variants in enumerate(search_terms(value)):
                alternatives = []
                for variant_index, variant in enumerate(variants):
                    key = f"{definition.prefix}_{term_index}_{variant_index}"
                    self.parameters[key] = f"%{variant}%"
                    alternatives.append(f"{folded} LIKE :{key}")
                predicates.append(alternatives[0] if len(alternatives) == 1 else "(" + " OR ".join(alternatives) + ")")
        return predicates

    def from_lines(self) -> list[str]:
        lines = [f"FROM {self.fact_table()} AS {self.fact.alias}"]
        if self.fact.official_window is not None:
            lines.append("CROSS JOIN bounds AS b")  # one row: the last month of the official window
        for join in self.joins():
            lines.append(f"{join.kind} JOIN {self.table(join.table)} AS {join.alias}")
            lines.append(f"    ON {join.on}")
        return lines

    @staticmethod
    def where_lines(predicates: list[str]) -> list[str]:
        if not predicates:
            return []
        return [f"WHERE {predicates[0]}"] + [f"    AND {predicate}" for predicate in predicates[1:]]

    # -- value columns ------------------------------------------------------
    def value_items(self) -> list[str]:
        m = self.metric
        if m.is_ratio:
            return [
                f"{m.numerator} AS {m.value_alias}_numerator",
                f"{m.denominator} AS {m.value_alias}_denominator",
                f"{m.numerator} / NULLIF({m.denominator}, 0) AS {m.value_alias}",
            ]
        return [f"{m.aggregate} AS {m.value_alias}"]

    def value_columns(self) -> list[ColumnSpec]:
        m = self.metric
        if m.is_ratio:
            return [
                ColumnSpec(f"{m.value_alias}_numerator", ColumnType.INT, ColumnRole.NUMERATOR),
                ColumnSpec(f"{m.value_alias}_denominator", ColumnType.INT, ColumnRole.DENOMINATOR),
                ColumnSpec(m.value_alias, ColumnType.DECIMAL, ColumnRole.METRIC, nullable=True),
            ]
        if m.order_level:
            return [
                ColumnSpec(f"{m.value_alias}_numerator", ColumnType.DECIMAL, ColumnRole.NUMERATOR),
                ColumnSpec(f"{m.value_alias}_denominator", ColumnType.INT, ColumnRole.DENOMINATOR),
                ColumnSpec(m.value_alias, ColumnType.DECIMAL, ColumnRole.METRIC, nullable=True),
            ]
        # A SUM/AVG over no rows is NULL ("no data"); a COUNT is 0. Grouped
        # rows always come from at least one source row, so their value is
        # only nullable for metrics that can be NULL by definition.
        empty_is_null = self.request.operation is Operation.AGGREGATE and not (m.aggregate or "").startswith("COUNT")
        return [ColumnSpec(m.value_alias, _numeric_type(m.unit), ColumnRole.METRIC, nullable=m.nullable_value or empty_is_null)]

    def value_names(self) -> list[str]:
        return [column.name for column in self.value_columns()]

    def dimension_specs(self) -> list[ColumnSpec]:
        return [
            ColumnSpec(column.name, column.type, ColumnRole.DIMENSION, nullable=column.nullable)
            for column in self.group_columns()
        ]

    def period_spec(self) -> list[ColumnSpec]:
        return [ColumnSpec("period", ColumnType.DATE, ColumnRole.PERIOD)] if self.period_expr() else []

    # -- grouped statement -------------------------------------------------
    def grouped_select(self) -> tuple[list[tuple[str, list[str]]], list[str]]:
        """CTEs needed before the grouped SELECT, and the grouped SELECT itself."""
        m = self.metric
        group_columns = self.group_columns()
        period = self.period_expr()
        predicates = self.predicates()

        group_items = ([f"{period} AS period"] if period else []) + [_select_item(c) for c in group_columns]
        group_exprs = ([period] if period else []) + [c.expr for c in group_columns]

        if m.order_level:
            inner_items = ["s.order_id"] + group_items + [f"MAX(s.order_total_amount) AS order_total"]
            orders = (
                ["SELECT"]
                + _indent(_comma(inner_items))
                + self.from_lines()
                + self.where_lines(predicates)
                + ["GROUP BY"]
                + _indent(_comma(["s.order_id"] + group_exprs))
            )
            outer_names = (["period"] if period else []) + [c.name for c in group_columns]
            items = outer_names + [
                f"SUM(order_total) AS {m.value_alias}_numerator",
                f"COUNT(*) AS {m.value_alias}_denominator",
                f"SUM(order_total) / NULLIF(COUNT(*), 0) AS {m.value_alias}",
            ]
            select = ["SELECT"] + _indent(_comma(items)) + ["FROM orders"]
            if outer_names:
                select += ["GROUP BY"] + _indent(_comma(outer_names))
            return [("orders", orders)], select

        items = group_items + self.value_items()
        select = ["SELECT"] + _indent(_comma(items)) + self.from_lines() + self.where_lines(predicates)
        if group_exprs:
            select += ["GROUP BY"] + _indent(_comma(group_exprs))
        if self.request.positive_only:
            select += [f"HAVING {m.aggregate} > 0"]
        return self.official_window_ctes(), select

    # -- official-window eligibility (model outputs) ---------------------------------------
    def _window_month(self) -> str:
        spec = self.fact.official_window
        return f"CAST({self.fact.alias}.{spec.month_column} AS DATE)"

    def _official_window_predicates(self) -> list[str]:
        spec = self.fact.official_window
        alias = self.fact.alias
        self.parameters["official_version"] = spec.official_version
        series = ", ".join(f"{alias}.{c}" for c in spec.series_columns)
        plain = ", ".join(spec.series_columns)
        return [
            f"({series}) IN (SELECT {plain} FROM eligible)",  # only series that pass the rule
            f"{alias}.{spec.version_column} = :official_version",
            f"{self._window_month()} BETWEEN add_months(b.window_end, -{spec.months - 1}) AND b.window_end",
            "(SELECT COUNT(*) FROM matched) = 1",  # the text filters name exactly one series
        ]

    def official_window_ctes(self) -> list[tuple[str, list[str]]]:
        """CTEs of a fact with an official window: ``bounds`` (the window's last
        month, from the official version's rows), ``matched`` (the series the
        name filters select) and ``eligible`` (those of them that pass the
        rule). Nothing here comes from the question except the filter terms,
        which are bound parameters."""
        spec = self.fact.official_window
        if spec is None:
            return []
        for identifier in (*spec.series_columns, spec.month_column, spec.version_column, spec.generated_at_column, spec.value_column):
            validate_identifier(identifier, label="official window column")
        alias = self.fact.alias
        table = self.fact_table()
        self.parameters["official_version"] = spec.official_version
        series = [f"{alias}.{c}" for c in spec.series_columns]
        same_series = " AND ".join(f"{alias}.{c} = m.{c}" for c in spec.series_columns)
        text = self._text_predicates()
        bounds = [
            "SELECT",
            f"    CAST(MAX({alias}.{spec.month_column}) AS DATE) AS window_end",
            f"FROM {table} AS {alias}",
            f"WHERE {alias}.{spec.version_column} = :official_version",
        ]
        matched = ["SELECT DISTINCT"] + _indent(_comma(series)) + [f"FROM {table} AS {alias}"] + self.where_lines(text)
        eligible = (
            ["SELECT"]
            + _indent(_comma(series))
            + [f"FROM {table} AS {alias}", "CROSS JOIN bounds AS b", "INNER JOIN matched AS m", f"    ON {same_series}"]
            + self.where_lines([f"{self._window_month()} BETWEEN add_months(b.window_end, -{spec.months - 1}) AND b.window_end"])
            + ["GROUP BY"]
            + _indent(_comma(series))
            + [
                f"HAVING COUNT(*) = {spec.months}",
                f"    AND COUNT(DISTINCT {self._window_month()}) = {spec.months}",
                f"    AND COUNT(CASE WHEN {alias}.{spec.version_column} = :official_version THEN 1 END) = {spec.months}",
                f"    AND COUNT(DISTINCT {alias}.{spec.generated_at_column}) = 1",
                f"    AND COUNT({alias}.{spec.value_column}) = {spec.months}",
            ]
        )
        return [("bounds", bounds), ("matched", matched), ("eligible", eligible)]

    # -- assembling ----------------------------------------------------------
    def compile_plain(self) -> tuple[str, list[ColumnSpec]]:
        request = self.request
        m = self.metric
        ctes, grouped = self.grouped_select()
        dim_specs = self.dimension_specs()
        value_specs = self.value_columns()
        dim_names = [c.name for c in self.group_columns()]

        if request.operation is Operation.RANKING:
            order = "ASC" if (request.ranking_order or m.ranking_order) is SortOrder.ASC else "DESC"
            self.parameters["top_n"] = DEFAULT_TOP_N if request.top_n is None else request.top_n
            carried = dim_names + self.value_names()
            if m.preserve_ties:
                rank_fn, order_by = DIMENSIONS[request.dimensions[0]].rank_function, f"{m.value_alias} {order}"
            else:
                # Bounded: exactly the first N by the exact value, ties broken by the
                # dimension's own columns (product_id, product_name, ...), ranks 1..N.
                rank_fn, order_by = "ROW_NUMBER", ", ".join([f"{m.value_alias} {order}"] + dim_names)
            window = [
                f"{rank_fn}() OVER (",
                f"    ORDER BY {order_by}",
                ") AS ranking",
            ]
            ranked = ["SELECT"] + _indent([f"{name}," for name in carried]) + _indent(window) + ["FROM grouped"]
            if m.nullable_value:
                ranked += [f"WHERE {m.value_alias} IS NOT NULL"]
            final = (
                ["SELECT"]
                + _indent(_comma(["ranking"] + dim_names + self.value_names()))
                + ["FROM ranked", "WHERE ranking <= :top_n", "ORDER BY"]
                + _indent(_comma(["ranking"] + dim_names))
            )
            statement = self._render(ctes + [("grouped", grouped), ("ranked", ranked)], final)
            columns = [ColumnSpec("ranking", ColumnType.INT, ColumnRole.RANK)] + dim_specs + value_specs
            return statement, columns

        if request.operation is Operation.TREND:
            final = grouped + ["ORDER BY", "    period"]
            return self._render(ctes, final), self.period_spec() + value_specs

        final = list(grouped)
        if request.operation is Operation.BREAKDOWN:
            binding = self.fact.bindings[request.dimensions[0]]
            if binding.order_by:
                order_items = list(dict.fromkeys([binding.order_by] + dim_names))
            else:
                nulls = " NULLS LAST" if m.nullable_value else ""
                order_items = [f"{m.value_alias} DESC{nulls}"] + dim_names
            final += ["ORDER BY"] + _indent(_comma(order_items))
        return self._render(ctes, final), dim_specs + value_specs

    @staticmethod
    def _render(ctes: list[tuple[str, list[str]]], final: list[str]) -> str:
        lines: list[str] = []
        for index, (name, body) in enumerate(ctes):
            lines.append(("WITH " if index == 0 else "") + f"{name} AS (")
            lines.extend(_indent(body))
            lines.append("),")
        if lines:
            lines[-1] = ")"
        return "\n".join(lines + final) + "\n"

    # -- time modifiers (only metrics that declare them in the registry) --------------------------------------
    def compile_modifier(self) -> tuple[str, list[ColumnSpec]]:
        request = self.request
        m = self.metric
        modifier = request.time_modifier
        date_col = self.fact.date_column
        inner = _sum_inner(m.aggregate or "")
        filters = request.filters
        predicates = self.predicates(include_date=False)
        specs = [ColumnSpec(m.value_alias, ColumnType.DECIMAL, ColumnRole.METRIC, nullable=True)]

        if modifier is TimeModifier.YOY:
            assert filters.date_range is not None
            self.parameters["date_from"] = filters.date_range.start.isoformat()
            self.parameters["date_to"] = filters.date_range.end.isoformat()
            current = f"{date_col} >= CAST(:date_from AS DATE) AND {date_col} <= CAST(:date_to AS DATE)"
            previous = (
                f"{date_col} >= add_months(CAST(:date_from AS DATE), -12) "
                f"AND {date_col} <= add_months(CAST(:date_to AS DATE), -12)"
            )
            window = [
                f"{date_col} >= add_months(CAST(:date_from AS DATE), -12)",
                f"{date_col} <= CAST(:date_to AS DATE)",
            ]
            periods = (
                ["SELECT"]
                + _indent(
                    _comma(
                        [
                            f"SUM(CASE WHEN {current} THEN {inner} END) AS current_value",
                            f"SUM(CASE WHEN {previous} THEN {inner} END) AS previous_value",
                        ]
                    )
                )
                + self.from_lines()
                + self.where_lines(predicates + window)
            )
            final = [
                "SELECT",
                f"    current_value AS {m.value_alias},",
                f"    previous_value AS {m.value_alias}_previous,",
                f"    (current_value - previous_value) / NULLIF(previous_value, 0) AS {m.value_alias}_yoy",
                "FROM periods",
            ]
            specs += [
                ColumnSpec(f"{m.value_alias}_previous", ColumnType.DECIMAL, ColumnRole.PREVIOUS, nullable=True),
                ColumnSpec(f"{m.value_alias}_yoy", ColumnType.DECIMAL, ColumnRole.CHANGE, nullable=True),
            ]
            return self._render([("periods", periods)], final), specs

        # YTD / LTM: Power BI evaluates them at the last date of the date
        # context (TOTALYTD; DATESINPERIOD(MAX(date), -12, MONTH)). The
        # start of the given period is replaced by the window, exactly like
        # the DAX does; the end is the anchor. A period is always required
        # (see check_compatibility), so nothing is ever assumed here.
        assert filters.date_range is not None
        self.parameters["date_to"] = filters.date_range.end.isoformat()
        anchor = "CAST(:date_to AS DATE)"
        window = (
            [f"{date_col} >= trunc({anchor}, 'YEAR')", f"{date_col} <= {anchor}"]
            if modifier is TimeModifier.YTD
            else [f"{date_col} > add_months({anchor}, -12)", f"{date_col} <= {anchor}"]
        )
        select = (
            ["SELECT", f"    {m.aggregate} AS {m.value_alias}"]
            + self.from_lines()
            + self.where_lines(predicates + window)
        )
        return self._render([], select), specs

    # -- filters as shown to the user ---------------------------------------
    def applied_filters(self) -> dict[str, Any]:
        request = self.request
        filters = request.filters
        if request.as_of is not None:
            period = f"as_of:{request.as_of.isoformat()}"
        elif self.metric.snapshot:
            period = "snapshot"
        elif request.time_modifier in (TimeModifier.YTD, TimeModifier.LTM):
            period = f"{request.time_modifier.value}@{filters.date_range.end.isoformat()}"
        elif filters.date_range is not None:
            period = f"{filters.date_range.start.isoformat()}..{filters.date_range.end.isoformat()}"
        else:
            period = "all"
        applied: dict[str, Any] = {"population": self.fact.population, "period": period, "location": "all"}
        if request.operation is Operation.RANKING:
            applied["top_n"] = self.parameters["top_n"]
        if request.time_modifier is TimeModifier.YOY:
            applied["comparison"] = "same_period_previous_year"
        if filters.categories:
            applied["categories"] = list(filters.categories)
        if filters.customer_segments:
            applied["customer_segments"] = list(filters.customer_segments)
        if filters.inventory_health_statuses:
            applied["inventory_health_statuses"] = list(filters.inventory_health_statuses)
        for kind, value in filters.text_filters().items():
            applied[kind.value.lower()] = value
        if request.positive_only:
            applied["positive_only"] = True
        if request.ranking_order is not None:
            applied["ranking_order"] = request.ranking_order.value
        return applied


def compile_request(
    request: AnalyticalRequest,
    *,
    catalog: str,
    schema: str,
    intent: StructuredIntent = StructuredIntent.ANALYTICS,
) -> PreparedQuery:
    """Validate ``request`` and compile it to one allowlisted statement with
    named parameters. Never executes anything."""
    validate_request(request)
    catalog = validate_identifier(catalog, label="catalog")
    schema = validate_identifier(schema, label="schema")

    builder = _Builder(request, catalog, schema)
    if request.time_modifier is not None:
        statement, columns = builder.compile_modifier()
    else:
        statement, columns = builder.compile_plain()

    source_tables = (f"{builder.fact_schema}.{builder.fact.table}",) + tuple(f"{schema}.{join.table}" for join in builder.joins())
    return PreparedQuery(
        intent=intent,
        statement=statement,
        parameters=dict(builder.parameters),
        applied_filters=builder.applied_filters(),
        source_tables=source_tables,
        request=request,
        columns=tuple(columns),
    )
