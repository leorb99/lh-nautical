"""Deterministic, friendly rendering of structured results.

Values that come from SQL are facts, so the text the user reads is produced
by plain code from the validated rows — never by a language model. That
guarantees every returned row (ties included), its rank, its label and its
value reach the user exactly as the query produced them, independent of any
model behavior or token limit.

The text is a natural Portuguese sentence or list — never the internal
semantic request ("AGGREGATE · NET_REVENUE"), which is metadata for logs and
debugging only. There are no per-question templates: the wording comes from
*presentation metadata in the registry* (``MetricDef.display_pt``,
``sentence_pt``, ``ranking_pt`` and the value format ``Unit``) and from the
dimension labels, combined by one renderer per operation:

- AGGREGATE  — one sentence ("O faturamento líquido foi de R$ 1.234.567,89.");
- RANKING    — a title and numbered lines ("Os 5 produtos com maior receita
  líquida foram:"); ties keep their shared rank;
- BREAKDOWN  — a short introduction and one line per group;
- TREND      — a short introduction and one line per period, chronological.

Pure and offline: no LLM, no I/O, no SQL. Number formatting (``1.737``,
``R$ 1.234,56``, ``12,34%``) only changes the notation, never the value
beyond the stated decimal places.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from src.structured.analytical_request import DEFAULT_TOP_N, AnalyticalRequest
from src.structured.models import (
    AnalyticalTable,
    ColumnRole,
    ColumnSpec,
    ColumnType,
    StructuredIntent,
    StructuredResult,
    TopProductRow,
    UnsupportedIntentError,
)
from src.structured.semantic_model import (
    DIMENSIONS,
    METRICS,
    Dimension,
    Metric,
    MetricDef,
    Operation,
    TimeGrain,
    TimeModifier,
    Unit,
)

__all__ = ["format_quantity", "render_structured_answer"]

_UNKNOWN_LABEL = "(não informado)"

_HEALTH_LABELS = {
    "out_of_stock": "Fora de estoque",
    "critical": "Crítico",
    "attention": "Atenção",
    "healthy": "Saudável",
    "no_recent_sales": "Sem vendas recentes",
}

_GRAIN_LABEL = {
    TimeGrain.DAY: "dia",
    TimeGrain.MONTH: "mês",
    TimeGrain.QUARTER: "trimestre",
    TimeGrain.YEAR: "ano",
}

_MONTHS = [
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
]  # fmt: skip
_MONTHS_SHORT = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]


# ---------------------------------------------------------------------------
# number and date formatting
# ---------------------------------------------------------------------------


def _group(integer_text: str) -> str:
    digits = integer_text.lstrip("-")
    grouped = ""
    while len(digits) > 3:
        grouped = "." + digits[-3:] + grouped
        digits = digits[:-3]
    return ("-" if integer_text.startswith("-") else "") + digits + grouped


def _format_int(value: int) -> str:
    return _group(str(value))


def _format_decimal(value: Decimal, places: int) -> str:
    quantized = value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    text = f"{quantized:f}"
    integer, _, fraction = text.partition(".")
    return _group(integer) + ("," + fraction if places else "")


def format_quantity(value: int) -> str:
    """Integer with ``.`` as the thousands separator (Portuguese style) and
    the correct singular/plural noun. Formatting only — the numeric value is
    never changed."""
    return f"{_format_int(value)} {'unidade' if value == 1 else 'unidades'}"


def format_decimal_units(value: Decimal) -> str:
    """An exact decimal quantity shown as whole physical units ("556 unidades",
    "1 unidade"). Rounded half up, for display only: the value itself, and the
    order of a ranking, always use the exact decimal. The noun follows the
    rounded number."""
    whole = int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return f"{_format_int(whole)} {'unidade' if whole == 1 else 'unidades'}"


def _format_value(value: object, metric: MetricDef) -> str:
    """The value in the metric's format (``MetricDef.unit``)."""
    if value is None:
        return "sem dados"
    unit = metric.unit
    if unit is Unit.CURRENCY_BRL:
        return "R$ " + _format_decimal(Decimal(value), 2)
    if unit is Unit.PERCENT:
        return _format_decimal(Decimal(value) * 100, 2) + "%"
    if unit is Unit.DAYS:
        return _format_decimal(Decimal(value), 1) + " dias"
    if unit is Unit.DECIMAL:
        return _format_decimal(Decimal(value), 2)
    if unit is Unit.UNITS:
        return format_quantity(int(value))
    if unit is Unit.DECIMAL_UNITS:
        return format_decimal_units(Decimal(value))
    return _format_int(int(value))  # INTEGER


def _format_signed_percent(ratio: Decimal) -> str:
    return ("+" if ratio > 0 else "") + _format_decimal(ratio * 100, 2) + "%"


def _format_date(value: date) -> str:
    return f"{value.day:02d}/{value.month:02d}/{value.year:04d}"


def _period_label(value: date, grain: TimeGrain) -> str:
    if grain is TimeGrain.MONTH:
        return f"{_MONTHS_SHORT[value.month - 1]}/{value.year:04d}"
    if grain is TimeGrain.QUARTER:
        return f"{(value.month - 1) // 3 + 1}º tri/{value.year:04d}"
    if grain is TimeGrain.YEAR:
        return f"{value.year:04d}"
    return _format_date(value)


# ---------------------------------------------------------------------------
# scope phrases ("em 2025", "na categoria Motores")
# ---------------------------------------------------------------------------


def _is_whole_month(start: date, end: date) -> bool:
    if (start.year, start.month) != (end.year, end.month) or start.day != 1:
        return False
    next_day = date.fromordinal(end.toordinal() + 1)
    return next_day.month != end.month


def _period_phrase(request) -> str:
    modifier = request.time_modifier
    date_range = request.filters.date_range
    if modifier in (TimeModifier.YTD, TimeModifier.LTM):
        # Anchored on the end of the period the user gave (never assumed).
        anchor = _format_date(date_range.end)
        return f" acumulado no ano até {anchor}" if modifier is TimeModifier.YTD else f" nos últimos 12 meses até {anchor}"
    if date_range is None:
        return ""
    start, end = date_range.start, date_range.end
    if (start.month, start.day, end.month, end.day) == (1, 1, 12, 31) and start.year == end.year:
        return f" em {start.year}"
    if start == end:
        return f" em {_format_date(start)}"
    if _is_whole_month(start, end):
        return f" em {_MONTHS[start.month - 1]} de {start.year}"
    return f" entre {_format_date(start)} e {_format_date(end)}"


def _join_pt(values: list[str]) -> str:
    return values[0] if len(values) == 1 else ", ".join(values[:-1]) + " e " + values[-1]


def _filter_phrase(request) -> str:
    filters = request.filters
    parts = []
    if filters.categories:
        parts.append(
            f"na categoria {filters.categories[0]}"
            if len(filters.categories) == 1
            else f"nas categorias {_join_pt(list(filters.categories))}"
        )
    if filters.customer_segments:
        parts.append(f"no segmento {_join_pt(list(filters.customer_segments))}")
    if filters.inventory_health_statuses:
        labels = [_HEALTH_LABELS.get(v, v).lower() for v in filters.inventory_health_statuses]
        parts.append(f"com status {_join_pt(labels)}")
    # Name filters, shown as the user wrote them: what the answer covers is never hidden.
    if filters.product_text:
        parts.append(f'de produtos com "{filters.product_text}" no nome')
    if filters.category_text:
        parts.append(f"na categoria {filters.category_text}")
    if filters.location_text:
        parts.append(f'no estabelecimento "{filters.location_text}"')
    return "".join(f" {part}" for part in parts)


def _scope(request) -> str:
    return _period_phrase(request) + _filter_phrase(request)


def _position_prefix(request, metric: MetricDef) -> str:
    """Which position of a stock the values describe: the date of a past one, or
    the current one as available in the system (never "updated today")."""
    if request.as_of is not None:
        return f"Na posição de estoque de {_format_date(request.as_of)}"
    return metric.position_pt


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:]


def _with_position(request, metric: MetricDef, sentence: str) -> str:
    """``Na posição atual disponível no sistema, o estoque ... é de ...``."""
    prefix = _position_prefix(request, metric)
    return f"{prefix}, {_lower_first(sentence)}" if prefix else sentence


# ---------------------------------------------------------------------------
# introductions of breakdowns and trends, generated from presentation metadata
# ---------------------------------------------------------------------------

#: Grain adjective by (masculine singular, feminine singular, masculine plural, feminine plural).
_GRAIN_ADJECTIVE = {
    TimeGrain.DAY: ("diário", "diária", "diários", "diárias"),
    TimeGrain.MONTH: ("mensal", "mensal", "mensais", "mensais"),
    TimeGrain.QUARTER: ("trimestral", "trimestral", "trimestrais", "trimestrais"),
    TimeGrain.YEAR: ("anual", "anual", "anuais", "anuais"),
}


def _agreement(metric: MetricDef) -> tuple[bool, bool]:
    """(feminine, plural) of the metric's noun phrase, read from the article
    that starts ``display_pt`` ("o faturamento", "as unidades vendidas")."""
    article = metric.display_pt.split()[0]
    return article in ("a", "as"), article in ("os", "as")


def _verb(metric: MetricDef, plural: bool, *, past: bool = False) -> str:
    """A snapshot is stated in the present ("é"), a period or a past position
    in the past ("foi")."""
    if metric.snapshot and not past:
        return "são" if plural else "é"
    return "foram" if plural else "foi"


def _grain_adjective(metric: MetricDef, grain: TimeGrain) -> str:
    feminine, plural = _agreement(metric)
    return _GRAIN_ADJECTIVE[grain][(2 if plural else 0) + (1 if feminine else 0)]


def _quarter(value: date) -> int:
    return (value.month - 1) // 3 + 1


def _trend_span(grain: TimeGrain, first: date, last: date) -> tuple[str, bool]:
    """How the periods really returned are introduced ("Ao longo de 2025",
    "De janeiro a agosto de 2026") and whether it is a single period."""
    if grain is TimeGrain.YEAR:
        return (f"Em {first.year}", True) if first.year == last.year else (f"De {first.year} a {last.year}", False)

    if grain is TimeGrain.QUARTER:
        q1, q2 = _quarter(first), _quarter(last)
        if (first.year, q1) == (last.year, q2):
            return f"No {q1}º trimestre de {first.year}", True
        if first.year == last.year:
            if (q1, q2) == (1, 4):
                return f"Ao longo de {first.year}", False
            return f"Do {q1}º ao {q2}º trimestre de {first.year}", False
        return f"Do {q1}º trimestre de {first.year} ao {q2}º trimestre de {last.year}", False

    if grain is TimeGrain.DAY:
        if first == last:
            return f"Em {_format_date(first)}", True
        if first.year == last.year and (first.month, first.day, last.month, last.day) == (1, 1, 12, 31):
            return f"Ao longo de {first.year}", False
        if _is_whole_month(first, last):
            return f"Em {_MONTHS[first.month - 1]} de {first.year}", False
        return f"De {_format_date(first)} a {_format_date(last)}", False

    # MONTH
    if (first.year, first.month) == (last.year, last.month):
        return f"Em {_MONTHS[first.month - 1]} de {first.year}", True
    if first.year == last.year:
        if (first.month, last.month) == (1, 12):
            return f"Ao longo de {first.year}", False
        return f"De {_MONTHS[first.month - 1]} a {_MONTHS[last.month - 1]} de {first.year}", False
    return f"De {_MONTHS[first.month - 1]} de {first.year} a {_MONTHS[last.month - 1]} de {last.year}", False


# ---------------------------------------------------------------------------
# table access
# ---------------------------------------------------------------------------


def _row_label(table: AnalyticalTable, row: tuple, dimension: Dimension) -> str:
    """The label of a row's dimension value(s): for a product, its name."""
    if dimension is Dimension.PRODUCT:
        return str(row[table.column_index("product_name")])
    first = next(i for i, column in enumerate(table.columns) if column.role is ColumnRole.DIMENSION)
    value = row[first]
    if value is None or value == "":
        return _UNKNOWN_LABEL
    if dimension is Dimension.INVENTORY_HEALTH_STATUS:
        return _HEALTH_LABELS.get(value, value)
    return str(value)


#: Ratios shown as the percentage alone. "N de M itens monitorados" is left
#: out until the grain of ``fct_inventory_replenishment`` (what one row is) is
#: validated: M would otherwise be presented as a count of items.
_RATIO_WITHOUT_COUNTS = frozenset({Metric.STOCKOUT_RISK, Metric.REPLENISHMENT_REQUIRED})


def _metric_text(table: AnalyticalTable, row: tuple, metric: MetricDef) -> str:
    """The value, plus 'N de M <noun>' next to a ratio (Power BI ``DIVIDE``)."""
    value = row[table.column_index(metric.value_alias)]
    text = _format_value(value, metric)
    if metric.is_ratio and value is not None and metric.metric not in _RATIO_WITHOUT_COUNTS:
        numerator = row[table.column_index(f"{metric.value_alias}_numerator")]
        denominator = row[table.column_index(f"{metric.value_alias}_denominator")]
        text += f" ({_format_int(numerator)} de {_format_int(denominator)} {metric.ratio_noun_pt})"
    return text


def _dimension_notes(request) -> list[str]:
    """Rules a dimension applies that the reader should know (e.g. which
    employees the salesperson ranking considers)."""
    return [DIMENSIONS[d].scope_note_pt for d in request.dimensions if DIMENSIONS[d].scope_note_pt]


def _capitalize(text: str) -> str:
    return text[:1].upper() + text[1:]


# ---------------------------------------------------------------------------
# renderers (one per operation)
# ---------------------------------------------------------------------------


def _sentence(metric: MetricDef, value, value_text: str, scope: str, *, past: bool = False) -> str:
    template = metric.historical_sentence_pt if past and metric.historical_sentence_pt else metric.sentence_pt
    if metric.sentence_one_pt and metric.unit in (Unit.INTEGER, Unit.UNITS) and value == 1:
        template = metric.sentence_one_pt
    return template.format(value=value_text, scope=scope)


def _render_aggregate(request, table: AnalyticalTable, metric: MetricDef) -> list[str]:
    row = table.rows[0]
    value = row[table.column_index(metric.value_alias)]
    scope = _scope(request)

    if request.time_modifier is TimeModifier.YOY:
        previous = row[table.column_index(f"{metric.value_alias}_previous")]
        change = row[table.column_index(f"{metric.value_alias}_yoy")]
        sentence = _sentence(metric, value, _format_value(value, metric), scope).rstrip(".")
        comparison = f", contra {_format_value(previous, metric)} no mesmo período do ano anterior"
        variation = (
            f" (variação de {_format_signed_percent(change)})"
            if change is not None
            else " (sem base de comparação: o período anterior não tem valor)"
        )
        return [sentence + comparison + variation + "."]

    past = request.as_of is not None
    return [_with_position(request, metric, _sentence(metric, value, _metric_text(table, row, metric), scope, past=past))]


def _render_trend(request, table: AnalyticalTable, metric: MetricDef) -> list[str]:
    period_index = table.column_index("period")
    rows = sorted(table.rows, key=lambda row: row[period_index])  # chronological, whatever order came back
    grain = request.time_grain
    span, single = _trend_span(grain, rows[0][period_index], rows[-1][period_index])
    _, plural = _agreement(metric)
    subject = metric.display_pt if single else f"{metric.display_pt} {_grain_adjective(metric, grain)}"
    lines = [f"{span}, {subject}{_filter_phrase(request)} {_verb(metric, plural)}:", ""]
    lines += [f"- {_period_label(row[period_index], grain)} — {_metric_text(table, row, metric)}" for row in rows]
    return lines


def _render_breakdown(request, table: AnalyticalTable, metric: MetricDef) -> list[str]:
    dimension = request.dimensions[0]
    _, plural = _agreement(metric)
    prefix = _position_prefix(request, metric) or _capitalize(_period_phrase(request).strip())
    subject = f"{prefix}, {metric.display_pt}" if prefix else metric.display_pt
    only = " (somente valores maiores que zero)" if request.positive_only else ""
    verb = _verb(metric, plural, past=request.as_of is not None)
    title = f"{subject} por {DIMENSIONS[dimension].label_pt}{_filter_phrase(request)}{only} {verb}:"
    lines = [_capitalize(title), ""]
    lines += [f"- {_row_label(table, row, dimension)} — {_metric_text(table, row, metric)}" for row in table.rows]
    return lines


def _render_ranking(request, table: AnalyticalTable, metric: MetricDef) -> list[str]:
    dimension = request.dimensions[0]
    definition = DIMENSIONS[dimension]
    n = DEFAULT_TOP_N if request.top_n is None else request.top_n
    shown = min(n, len(table.rows))  # fewer than asked when there are fewer items
    scope = _scope(request)
    article = "A" if definition.feminine else "O"
    reversed_order = request.ranking_order is not None and request.ranking_order is not metric.ranking_order
    by = metric.ranking_reverse_pt if reversed_order else metric.ranking_pt
    only = " (somente valores maiores que zero)" if request.positive_only else ""
    if shown == 1:
        title = f"{article} {definition.label_pt} com {by}{scope}{only} foi:"
    else:
        title = f"{article}s {shown} {definition.plural_pt} com {by}{scope}{only} foram:"
    title = _with_position(request, metric, title)

    rank_index = table.column_index("ranking")
    lines = [title, ""]
    lines += [
        f"{row[rank_index]}. {_row_label(table, row, dimension)} — {_metric_text(table, row, metric)}" for row in table.rows
    ]
    ranks = [row[rank_index] for row in table.rows]
    if metric.preserve_ties and len(set(ranks)) < len(ranks):  # ties keep their shared rank, and the text says so
        adjective = "empatadas" if definition.feminine else "empatados"
        note = f"{_capitalize(definition.plural_pt)} {adjective} compartilham a mesma posição"
        if len(ranks) > n:
            note += ", por isso a lista tem mais itens do que o pedido"
        lines += ["", note + "."]
    return lines


def _source_note(result: StructuredResult) -> list[str]:
    """``Fonte: camada Gold — fct_sales + dim_products.``: the tables the query
    actually read, by table name only (no catalog or schema of the environment).
    Deterministic and separate from documentary ``[Sx]`` citations."""
    tables = tuple(dict.fromkeys(name.rsplit(".", 1)[-1] for name in result.source_tables))
    return [f"Fonte: camada Gold — {' + '.join(tables)}."] if tables else []


def _render_analytical(result: StructuredResult) -> str:
    table = result.table
    request = result.request
    metric = METRICS[request.metric]
    operation = request.operation

    if operation is Operation.AGGREGATE:
        lines = _render_aggregate(request, table, metric)
    elif operation is Operation.TREND:
        lines = _render_trend(request, table, metric)
    elif operation is Operation.BREAKDOWN:
        lines = _render_breakdown(request, table, metric)
    else:  # RANKING
        lines = _render_ranking(request, table, metric)

    return "\n".join(lines + [""] + _dimension_notes(request) + _source_note(result)).rstrip()


def _legacy_table(rows: tuple[TopProductRow, ...]) -> AnalyticalTable:
    columns = (
        ColumnSpec("ranking", ColumnType.INT, ColumnRole.RANK),
        ColumnSpec("product_id", ColumnType.INT, ColumnRole.DIMENSION),
        ColumnSpec("product_name", ColumnType.TEXT, ColumnRole.DIMENSION),
        ColumnSpec("quantity_sold", ColumnType.INT, ColumnRole.METRIC),
    )
    return AnalyticalTable(columns, tuple((r.rank, r.product_id, r.product_name, r.quantity_sold) for r in rows))


def render_structured_answer(result: StructuredResult) -> str:
    """The final answer text for a non-empty :class:`StructuredResult`.

    Rows are emitted in the order received (a trend is sorted by period),
    with the rank the query assigned (tied items repeat their shared rank).
    Nothing is dropped, added or re-ranked.
    """
    if result.table is not None and result.request is not None:
        return _render_analytical(result)
    if result.intent is StructuredIntent.TOP_PRODUCTS_BY_QUANTITY:
        # A hand-built Phase 6B.1 result: rendered by the same code as its equivalent request.
        request = AnalyticalRequest(Metric.TOTAL_QUANTITY_SOLD, Operation.RANKING, (Dimension.PRODUCT,))
        return _render_analytical(
            StructuredResult(
                intent=result.intent,
                rows=result.rows,
                row_count=result.row_count,
                source_tables=result.source_tables,
                catalog=result.catalog,
                request=request,
                table=_legacy_table(result.rows),
            )
        )
    raise UnsupportedIntentError(f"no renderer for intent {result.intent!r}")
