"""Natural language -> ``AnalyticalRequest`` (or a controlled refusal).

One small, deterministic, vocabulary-driven parser — not one regex family
per question. It only *selects* things the registry already approves
(:mod:`src.structured.semantic_model`): a metric, an operation, dimensions,
a period, a time modifier, a ``top_n``. It never produces SQL, table names
or column names, and every request it builds is checked against the
compatibility matrix (:func:`~src.structured.analytical_request.
check_compatibility`) before it is returned.

Three outcomes (:class:`ParseOutcome`):

- ``request`` set: a valid, supported request;
- ``unsupported_reason`` set: the question clearly asks for live values this
  layer cannot produce (an unapproved metric or combination, personal data,
  an ambiguous or relative period, "menos vendidos", ...). The Harness
  answers with a controlled "unsupported" — it never falls back to
  documentation for a data question;
- neither: not an analytical question at all (the router then keeps its
  documentary default).

Input is the normalized text of the *data clause* of a question (lowercase,
no accents, no punctuation — see ``source_router.normalize_query``).

Deliberately **not** in this parser: LLM classification. The vocabulary is
where a semantic-extraction step could later plug in, but whatever produces a
request, it must still pass the same validation, so an LLM could only ever
choose among approved parts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from src.structured.analytical_request import (
    AnalyticalRequest,
    DateRange,
    RequestFilters,
    check_compatibility,
)
from src.structured.models import UnsupportedRequestError
from src.structured.semantic_model import (
    Dimension,
    Metric,
    Operation,
    TimeGrain,
    TimeModifier,
)

__all__ = ["ParseOutcome", "parse_analytical_request"]


@dataclass(frozen=True)
class ParseOutcome:
    request: AnalyticalRequest | None = None
    unsupported_reason: str | None = None


_NOT_ANALYTICAL = ParseOutcome()


def _unsupported(reason: str) -> ParseOutcome:
    return ParseOutcome(unsupported_reason=reason)


def _c(*patterns: str) -> list[re.Pattern[str]]:
    return [re.compile(pattern) for pattern in patterns]


def _any(patterns: list[re.Pattern[str]], text: str) -> bool:
    return any(pattern.search(text) for pattern in patterns)


_NUMBER_WORDS = {
    "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5,
    "seis": 6, "sete": 7, "oito": 8, "nove": 9, "dez": 10,
}  # fmt: skip

_ENTITY_ALTERNATION = r"produtos?|itens?|item|skus?|categorias?|lojas?|estabelecimentos?|filiais|filial|vendedor(?:es)?|vendedoras?"
_ENTITY_WORDS = rf"(?:{_ENTITY_ALTERNATION})"

# ---------------------------------------------------------------------------
# refusals that do not depend on the metric
# ---------------------------------------------------------------------------

# Questions about the documentation / the model itself, not about live values.
_DOCUMENTARY_OBJECTS = _c(
    r"\b(documentos?|regras?|definicao|definicoes|glossario|dax|power ?bi|dashboard|painel|relatorio|"
    r"medidas?|metricas?|tabelas?|colunas?|campos?|dbt|gold|silver|bronze|camada|pipeline|"
    r"modelo semantico|formula|calculo|calcula|calculad[oa]s?|considerad[oa]s?|significa|conceito|politica|"
    r"granularidade|grao|serie historica|forecasting|lead time|prevalece|acontece quando|decisoes|declarad[oa]s?|observad[oa]s?)\b",
    r"\busad[oa]s? (?:no|na|em|para|nos|nas)\b",
)

_PII = _c(r"\b(e ?mails?|telefones?|celular|fone|cpf|cnpj|endereco|contato|whatsapp)\b")
_PII_REASON = "personal data (email, telephone, address, documents) is never exposed"

_UNSUPPORTED_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bmenos vendid|\bvend\w* menos\b|\bmenor(?:es)? (?:receita|venda|vendas|faturamento|quantidade)\b|\bpior(?:es)?\b"),
     'ascending rankings ("menos vendidos", lowest revenue) are not supported'),
    (re.compile(r"\bprevisao|\bprever\b|\bprojecao|\bforecast|\bdemanda futura"),
     "demand forecasts are not part of the supported analytics"),
    (re.compile(r"\bclientes? (?:que )?(?:mais|menos) (?:compr|gast|vend)|\bclientes?\b.{0,25}\b(?:compr|gast)\w* (?:mais|menos)\b|\b(?:melhores|maiores|principais|top) clientes\b"),
     "customer rankings are not supported (customer-level data is personal)"),
    (re.compile(r"\blucro|\bmargem|\bimpostos?\b|\bicms\b|\bipi\b|\bdescontos?\b|\bfrete\b"),
     "this financial metric is not supported"),
]

# ---------------------------------------------------------------------------
# metrics (first match wins: order encodes specificity)
# ---------------------------------------------------------------------------

_METRIC_PATTERNS: list[tuple[Metric, re.Pattern[str]]] = [
    (Metric.AT_RISK_REVENUE, re.compile(r"\breceita (?:em|de) (?:risco|churn)\b|\b(?:valor|ltv) em risco\b")),
    (Metric.AT_RISK_CUSTOMERS, re.compile(r"\bclientes?\b.{0,20}\b(?:em|at) ?risco\b|\bclientes? at risk\b|\brisco de churn\b")),
    (Metric.CUSTOMER_COUNT, re.compile(r"\b(?:total|numero|quantidade) de clientes\b|\bquantos clientes\b|\bbase de clientes\b")),
    (Metric.AVERAGE_RECENCY, re.compile(r"\brecencia\b")),
    (Metric.DAYS_TO_STOCKOUT, re.compile(r"\bdias (?:ate|para|de) (?:a )?ruptura\b|\bcobertura de estoque\b|\bdias de cobertura\b")),
    (Metric.STOCKOUT_RISK, re.compile(r"\brisco de ruptura\b|\bruptura\b|\b(?:vao|vai) faltar\b|\bem falta\b")),
    (Metric.REPLENISHMENT_REQUIRED, re.compile(r"\breposicao\b|\brepor\b|\breabastec\w*")),
    (Metric.INVENTORY_ITEMS, re.compile(
        r"\bstatus de saude\b|\bsaude (?:do )?estoque\b|\bitens? (?:criticos?|saudaveis|em atencao|sem estoque|fora de estoque|sem vendas recentes)\b"
    )),
    (Metric.STOCK_ON_HAND, re.compile(
        r"\bestoque (?:atual|total|disponivel)\b|\bestoque\b.{0,15}\b(?:mensal|por mes|por dia|ao longo do tempo)\b|"
        r"\b(?:evolucao|tendencia)\b.{0,15}\bestoque\b|\b(?:quanto|quantos|quantas)\b.{0,25}\bem estoque\b|\bquantidade em estoque\b|\bunidades em estoque\b"
    )),
    (Metric.RETURN_RATE, re.compile(r"\b(?:taxa|indice|percentual|proporcao) de (?:devolucao|devolucoes|retorno)\b")),
    (Metric.RETURN_AMOUNT, re.compile(
        r"\breembols\w*|\bvalor (?:devolvido|das devolucoes)\b|\bdevolucoes? em (?:valor|reais)\b|\bquanto (?:foi )?(?:devolvido|reembolsado)\b|\btotal (?:devolvido|de devolucoes)\b"
    )),
    (Metric.AVERAGE_TICKET, re.compile(r"\bticket\b|\bvalor medio (?:por|do|dos) pedidos?\b")),
    (Metric.EFFECTIVE_ORDERS, re.compile(r"\b(?:quantos|numero de|total de|quantidade de) pedidos\b|\bpedidos efetivos\b")),
    (Metric.NET_REVENUE, re.compile(r"\breceita\b|\bfaturamento\b|\bfaturamos\b|\bfaturou\b|\bfaturaram\b|\bfaturar\b|\bfatura\b")),
    (Metric.TOTAL_QUANTITY_SOLD, re.compile(
        r"\bunidades vendidas\b|\bquantidade (?:total )?(?:vendida|de itens vendidos)\b|\bquantas unidades\b|"
        r"\bquantos itens (?:vendemos|foram vendidos)\b|\bitens vendidos\b|\bmais vendid[oa]s?\b|"
        r"\b(?:vendeu|venderam|vende|vendem|vendemos) mais\b|\bmais (?:vendeu|venderam|vende|vendem|vendemos)\b|\bmais saem\b|\bmais unidades\b|"
        r"\b(?:produtos?|itens?) mais (?:popular(?:es)?|comprad[oa]s?|procurad[oa]s?)\b|\bvolume de vendas\b|"
        r"\bquantas? vendas\b|\bunidades vendemos\b|\bmelhores (?:produtos|itens) em vendas\b"
    )),
]

# "vendas" alone does not say whether R$ (net revenue) or units is meant.
_AMBIGUOUS_SALES = re.compile(r"\bquanto (?:nos )?(?:vendemos|vendeu|vendem)\b|\b(?:total|valor|numero) de vendas\b|\bvendas (?:totais|por)\b")

_CHAMPION = re.compile(r"\bcampea[oe]s?\b")

# ---------------------------------------------------------------------------
# operation cues
# ---------------------------------------------------------------------------

_DATA_REQUEST = _c(
    r"\b(?:quanto|quantos|quantas|quais|qual|quem)\b",
    r"\b(?:liste|listar|mostre|mostrar|exiba|exibir)\b",
    r"\bme (?:diga|mostre|passe|traga)\b",
    r"\btop ?\d*\b",
    r"\branking\b",
    r"\b(?:evolucao|tendencia)\b",
    r"\b(?:maiores|melhores|principais)\b",
    r"\bpor (?:categoria|loja|estabelecimento|filial|canal|vendedor|segmento|status|estado|mes|dia|trimestre|ano)\b",
    r"\b(?:19|20)\d{2}\b",
    r"\b(?:mensal|mensalmente|diari[oa]s?|anual|trimestral)\b",
    r"\bmais (?:vendid|unidades|popular|comprad|procurad)",
    r"\bcampea[oe]s?\b",
    r"\bytd\b|\bltm\b|\bultimos 12 meses\b|\bacumulad[oa]\b|\bcrescimento\b",
)

_RANKING_CUES = _c(
    r"\btop ?\d*\b",
    r"\branking\b",
    r"\bmais vendid",
    r"\b(?:maiores|melhores|principais)\b",
    r"\bque mais\b",
    r"\b(?:vendeu|venderam|vende|vendem|vendemos|faturou|faturaram|fatura|gerou|geraram|gera|geram) mais\b",
    r"\bmais (?:unidades|receita|faturamento|saem)\b",
    rf"\bqual (?:e |foi )?(?:o |a )?{_ENTITY_WORDS}\b",
    rf"\bquais (?:sao )?(?:os |as )?(?:\d+ |[a-z]+ )?{_ENTITY_WORDS}\b",
    r"\b(?:produtos?|itens?) mais (?:popular|comprad|procurad)",
)

_ENTITY_DIMENSIONS: list[tuple[re.Pattern[str], Dimension]] = [
    (re.compile(r"\b(?:produtos?|itens?|item|skus?)\b"), Dimension.PRODUCT),
    (re.compile(r"\bcategorias?\b"), Dimension.CATEGORY),
    (re.compile(r"\b(?:lojas?|estabelecimentos?|filiais|filial)\b"), Dimension.LOCATION),
    (re.compile(r"\b(?:vendedor(?:es)?|vendedoras?)\b"), Dimension.SALESPERSON),
]

_BY_PHRASE = re.compile(
    r"\bpor (categorias?|lojas?|estabelecimentos?|filiais|filial|canal|canais|vendedor(?:es)?|segmentos?|status|situacao|saude|"
    r"estados?|regioes|regiao|produtos?|itens?|clientes?|cidades?)\b(?: (?:do|da|de|dos|das) (lojas?|estabelecimentos?|filiais?|clientes?))?"
)

_BY_DIMENSION = {
    "categoria": Dimension.CATEGORY, "categorias": Dimension.CATEGORY,
    "loja": Dimension.LOCATION, "lojas": Dimension.LOCATION,
    "estabelecimento": Dimension.LOCATION, "estabelecimentos": Dimension.LOCATION,
    "filial": Dimension.LOCATION, "filiais": Dimension.LOCATION,
    "canal": Dimension.CHANNEL, "canais": Dimension.CHANNEL,
    "vendedor": Dimension.SALESPERSON, "vendedores": Dimension.SALESPERSON,
    "segmento": Dimension.CUSTOMER_SEGMENT, "segmentos": Dimension.CUSTOMER_SEGMENT,
    "status": Dimension.INVENTORY_HEALTH_STATUS, "situacao": Dimension.INVENTORY_HEALTH_STATUS,
    "saude": Dimension.INVENTORY_HEALTH_STATUS,
    "produto": Dimension.PRODUCT, "produtos": Dimension.PRODUCT, "item": Dimension.PRODUCT, "itens": Dimension.PRODUCT,
}  # fmt: skip

_TREND_PATTERNS: list[tuple[TimeGrain, re.Pattern[str]]] = [
    (TimeGrain.DAY, re.compile(r"\bpor dia\b|\bdiari[oa]s?\b|\bdia a dia\b|\bdiariamente\b")),
    (TimeGrain.QUARTER, re.compile(r"\bpor trimestre\b|\btrimestral(?:mente)?\b|\btrimestre a trimestre\b")),
    (TimeGrain.YEAR, re.compile(r"\bpor ano\b|\banual(?:mente)?\b|\bano a ano\b")),
    (TimeGrain.MONTH, re.compile(r"\bpor mes\b|\bmensal(?:mente)?\b|\bmes a mes\b")),
]
_TREND_GENERIC = re.compile(r"\b(?:evolucao|tendencia|ao longo do tempo|serie temporal)\b")

_MODIFIERS: list[tuple[TimeModifier, re.Pattern[str]]] = [
    (TimeModifier.YOY, re.compile(
        r"\byoy\b|\bano anterior\b|\bmesmo periodo do ano\b|\bcrescimento(?: anual)?\b|\bvariacao (?:anual|yoy|em relacao)\b"
    )),
    (TimeModifier.YTD, re.compile(r"\bytd\b|\bacumulad[oa] (?:no|do) ano\b|\bno ano ate (?:agora|hoje|o momento)\b|\bdesde o inicio do ano\b")),
    (TimeModifier.LTM, re.compile(r"\bltm\b|\bultimos 12 meses\b|\b12 ultimos meses\b|\b12 meses corridos\b")),
]

# ---------------------------------------------------------------------------
# periods
# ---------------------------------------------------------------------------

_MONTHS = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6,
    "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
}  # fmt: skip
_MONTH_YEAR = re.compile(r"\b(" + "|".join(_MONTHS) + r")(?: de)? ((?:19|20)\d{2})\b")
_YEAR_RANGE = re.compile(r"\b(?:de|entre) ((?:19|20)\d{2}) (?:a|e|ate) ((?:19|20)\d{2})\b")
_YEAR = re.compile(r"\b((?:19|20)\d{2})\b")
_MONTH_NAME = re.compile(r"\b(" + "|".join(_MONTHS) + r")\b")

# What is left after the recognized constructs are removed and still speaks
# about time or about filtering: refuse instead of silently ignoring it.
_LEFTOVER_PERIOD = re.compile(
    r"\b(?:hoje|ontem|amanha|semana|semanas|mes|meses|trimestre|semestre|periodo|recente|recentes|"
    r"ultimo|ultima|ultimos|ultimas|atual|passado|passada|dia|dias|ano|anos|desde|ate|apos|depois|antes|durante|entre|"
    r"verao|inverno|outono|primavera|natal|feriado|feriados)\b"
)
_LEFTOVER_FILTER = re.compile(
    r"\b(?:na|no|nas|nos|da|do|das|dos|em|para)\s+(?:loja|filial|estabelecimento|estado|categoria|canal|regiao|cidade|"
    r"segmento|vendedor|produto|item|sku|cliente)\b"
)

_TOP_N_PATTERNS = _c(
    r"\btop ?(\d{1,4})\b",
    rf"\b(?:os|as) (\d{{1,4}}) (?:{_ENTITY_ALTERNATION}|mais)\b",
    rf"\b(\d{{1,4}}) {_ENTITY_WORDS}\b",
)
_TOP_N_WORD = re.compile(r"\b(?:top |os |as )(" + "|".join(_NUMBER_WORDS) + r")\b")
_SINGULAR_SUBJECT = re.compile(
    r"\bqual (?:e |foi )?(?:o |a )?(?:produto|item|categoria|loja|estabelecimento|vendedor|sku)\b|"
    r"\b(?:produto|item|categoria|loja|estabelecimento|vendedor) (?:que )?mais\b|\bmais vendid[oa]\b(?! ?s)"
)


def _extract_top_n(text: str) -> int | None:
    for pattern in _TOP_N_PATTERNS:
        match = pattern.search(text)
        if match:
            return int(match.group(1))
    word = _TOP_N_WORD.search(text)
    return _NUMBER_WORDS[word.group(1)] if word else None


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------


def _detect_metric(text: str) -> tuple[Metric | None, re.Pattern[str] | None]:
    for metric, pattern in _METRIC_PATTERNS:
        if pattern.search(text):
            return metric, pattern
    return None, None


def _detect_grain(text: str) -> TimeGrain | None:
    for grain, pattern in _TREND_PATTERNS:
        if pattern.search(text):
            return grain
    return TimeGrain.MONTH if _TREND_GENERIC.search(text) else None


def _detect_period(text: str) -> tuple[DateRange | None, str | None, str]:
    """(date_range, refusal, text with the recognized period removed)."""
    working = text
    ranges: list[DateRange] = []

    for match in _MONTH_YEAR.finditer(working):
        month, year = _MONTHS[match.group(1)], int(match.group(2))
        end_day = (date(year + (month == 12), month % 12 + 1, 1) - date(year, month, 1)).days
        ranges.append(DateRange(date(year, month, 1), date(year, month, end_day)))
    working = _MONTH_YEAR.sub(" ", working)

    range_match = _YEAR_RANGE.search(working)
    if range_match:
        first, last = int(range_match.group(1)), int(range_match.group(2))
        ranges.append(DateRange(date(min(first, last), 1, 1), date(max(first, last), 12, 31)))
        working = _YEAR_RANGE.sub(" ", working)

    years = sorted({int(y) for y in _YEAR.findall(working)})
    if years:
        if len(years) > 1:
            return None, "several separate years in one question are not supported (use 'de A a B')", working
        ranges.append(DateRange(date(years[0], 1, 1), date(years[0], 12, 31)))
        working = _YEAR.sub(" ", working)

    if len(ranges) > 1:
        return None, "the period is ambiguous", working
    return (ranges[0] if ranges else None), None, working


def parse_analytical_request(text: str) -> ParseOutcome:
    """Map the normalized data clause of a question to an approved request,
    a controlled refusal, or "not analytical"."""
    t = text.strip()
    if not t or _any(_DOCUMENTARY_OBJECTS, t):
        return _NOT_ANALYTICAL
    if _any(_PII, t):
        return _unsupported(_PII_REASON)

    champion = bool(_CHAMPION.search(t))
    metric, metric_pattern = _detect_metric(t)
    if metric is None and champion:
        metric = Metric.NET_REVENUE  # Power BI "Produto Campeão" = highest net revenue (TOPN on [Receita Líquida])

    for pattern, reason in _UNSUPPORTED_PATTERNS:
        if pattern.search(t):
            return _unsupported(reason)
    if metric is None:
        return _unsupported("ambiguous metric: say 'receita' (R$) or 'unidades vendidas'") if _AMBIGUOUS_SALES.search(t) else _NOT_ANALYTICAL
    has_cue = champion or _any(_DATA_REQUEST, t)

    # -- what is being asked ------------------------------------------------
    grain = _detect_grain(t)
    modifier = next((m for m, pattern in _MODIFIERS if pattern.search(t)), None)

    by_phrases = list(_BY_PHRASE.finditer(t))
    by_dimensions: list[Dimension] = []
    for match in by_phrases:
        head, qualifier = match.group(1), match.group(2)
        if head in ("estado", "estados"):
            if qualifier and re.match(r"(?:loja|estabelecimento|filial)", qualifier):
                by_dimensions.append(Dimension.LOCATION_STATE)
            elif qualifier and qualifier.startswith("cliente"):
                by_dimensions.append(Dimension.CUSTOMER_STATE)
            else:
                return _unsupported("'estado' is ambiguous: say 'estado do estabelecimento' or 'estado do cliente'")
        elif head in _BY_DIMENSION:
            by_dimensions.append(_BY_DIMENSION[head])
        elif head not in ("mes", "dia", "trimestre", "ano"):
            return _unsupported(f"breakdown by '{head}' is not supported")

    # Remove what has been understood; whatever period/filter wording is left
    # is refused. (The metric's own words, e.g. "estoque atual", are not
    # period or filter wording.)
    working = metric_pattern.sub(" ", t) if metric_pattern is not None else t
    for _, pattern in _TREND_PATTERNS:
        working = pattern.sub(" ", working)
    working = _TREND_GENERIC.sub(" ", working)
    working = _BY_PHRASE.sub(" ", working)
    for _, pattern in _MODIFIERS:
        working = pattern.sub(" ", working)
    working = _CHAMPION.sub(" ", working)
    if modifier in (TimeModifier.YTD, TimeModifier.LTM):
        working = re.sub(r"\bate\b", " ", working)  # "acumulada no ano até março de 2025": the anchor is the period's end

    date_range, refusal, working = _detect_period(working)
    if refusal:
        return _unsupported(refusal)
    month_without_year = bool(_MONTH_NAME.search(working))
    relative_period = bool(_LEFTOVER_PERIOD.search(working))
    specific_filter = bool(_LEFTOVER_FILTER.search(working))
    # A metric with no question word ("Receita da loja X") is still a data
    # request when it carries scope wording: refuse it instead of leaving it
    # to documentation.
    scoped = bool(by_phrases or grain or modifier or date_range or month_without_year or relative_period or specific_filter)
    if not (has_cue or scoped):
        return _NOT_ANALYTICAL
    if month_without_year:
        return _unsupported("a month without a year is ambiguous")
    if relative_period:
        return _unsupported("relative or unrecognized periods are not supported: give a year (e.g. 'em 2025')")
    if specific_filter:
        return _unsupported("filtering by a specific store, state, category, product, channel or person is not supported")

    entities = [dim for pattern, dim in _ENTITY_DIMENSIONS if pattern.search(working)]
    ranking = champion or _any(_RANKING_CUES, t)

    # -- build ---------------------------------------------------------------
    top_n: int | None = None
    time_grain: TimeGrain | None = None
    if grain is not None:
        operation, dimensions, time_grain = Operation.TREND, (), grain
    elif ranking:
        if len(by_dimensions) > 1 or (by_dimensions and entities and by_dimensions[0] not in entities):
            return _unsupported("a ranking by more than one dimension is not supported")
        subject = by_dimensions or entities[:1] or [Dimension.PRODUCT]
        operation, dimensions = Operation.RANKING, (subject[0],)
        top_n = 1 if champion else _extract_top_n(t)
        if top_n is None and _SINGULAR_SUBJECT.search(t):
            top_n = 1
    elif by_dimensions:
        if len(by_dimensions) > 1:
            return _unsupported("a breakdown by more than one dimension is not supported")
        operation, dimensions = Operation.BREAKDOWN, (by_dimensions[0],)
    else:
        operation, dimensions = Operation.AGGREGATE, ()

    request = AnalyticalRequest(
        metric=metric,
        operation=operation,
        dimensions=dimensions,
        filters=RequestFilters(date_range=date_range),
        time_grain=time_grain,
        time_modifier=modifier,
        top_n=top_n,
    )
    try:
        check_compatibility(request)
    except UnsupportedRequestError as exc:
        return _unsupported(str(exc))
    return ParseOutcome(request=request)
