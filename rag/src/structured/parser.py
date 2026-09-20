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

import calendar
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
from src.structured.qualifiers import extract_qualifiers, item_after_quantity_word
from src.structured.semantic_model import (
    METRICS,
    Dimension,
    FilterKind,
    Metric,
    MetricFamily,
    Operation,
    SortOrder,
    TimeGrain,
    TimeModifier,
)
from src.structured.text_match import MAX_TEXT_LENGTH, normalize_text

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

# "quantos vendedores temos?" is not a question about the stock of an item.
_NOT_ITEMS = frozenset(
    "vendedor vendedores vendedora vendedoras funcionario funcionarios empregado empregados colaborador colaboradores "
    "cliente clientes pedido pedidos fornecedor fornecedores usuario usuarios pessoa pessoas venda vendas".split()
)

_PII = _c(r"\b(e ?mails?|telefones?|celular|fone|cpf|cnpj|endereco|contato|whatsapp)\b")
_PII_REASON = "personal data (email, telephone, address, documents) is never exposed"

_FORECAST_REFUSAL = "demand forecasts are not part of the supported analytics"
_FORECAST_SHAPE_REFUSAL = (
    "a demand forecast (P85) is available for exactly one product in exactly one store over the next 3 months "
    "(e.g. 'previsão do <produto> na <loja> nos próximos 3 meses'); totals, rankings, other periods and "
    "several products or stores are not supported"
)

_UNSUPPORTED_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bmenos vendid|\bvend\w* menos\b|\bmenor(?:es)? (?:receita|venda|vendas|faturamento|quantidade)\b|\bpior(?:es)?\b"),
     'ascending rankings ("menos vendidos", lowest revenue) are not supported'),
    (re.compile(r"\bprevisao|\bprever\b|\bprojecao|\bforecast|\bdemanda futura"),
     _FORECAST_REFUSAL),
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
        r"\bunidades vendidas\b|\bquantidade (?:total )?(?:vendida|de itens vendidos)\b|\bquantas unidades\b(?!.{0,40}\b(?:temos|tem|ha|havia|existem|restam|sobram|estoque)\b)|"
        r"\bquantos itens (?:vendemos|foram vendidos)\b|\bitens vendidos\b|\bmais vendid[oa]s?\b|"
        r"\b(?:vendeu|venderam|vende|vendem|vendemos) mais\b|\bmais (?:vendeu|venderam|vende|vendem|vendemos)\b|\bmais saem\b|\bmais unidades\b|"
        r"\b(?:produtos?|itens?) mais (?:popular(?:es)?|comprad[oa]s?|procurad[oa]s?)\b|\bvolume de vendas\b|"
        r"\bquantas? vendas\b|\bunidades vendemos\b|\bmelhores (?:produtos|itens) em vendas\b"
    )),
    # The P85 forecast of one product in one store. After every other metric: "previsão de receita" is
    # still a refused forecast of revenue, not this one.
    (Metric.DEMAND_FORECAST_P85, re.compile(
        r"\bprevisao\b|\bprevisoes\b|\bprojecao\b|\bdemanda (?:prevista|futura)\b|\bprever\b|\bforecast\b|"
        r"\bdemanda\b.{0,60}\bproximos?\b"
    )),
    # Last resort (every other metric had its chance): a question about stock that names
    # the stock ("qual o estoque por estado", "em quais estoques ...") or asks how many of
    # something "we have" ("quantos coletes temos?", "quanto temos do X?").
    (Metric.STOCK_ON_HAND, re.compile(
        r"\bestoques?\b|\b(?:quanto|quantos|quantas)\b.{0,80}\b(?:temos|tem|ha|havia|existem|existe|restam|sobram|tinhamos|possuimos)\b|"
        r"\bonde\b.{0,20}\b(?:temos|tem|ha|havia|existem|existe|tinhamos)\b"
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
    r"\b(?:por|em cada|para cada|de cada|por cada) (categorias?|lojas?|estabelecimentos?|filiais|filial|canal|canais|vendedor(?:es)?|segmentos?|status|situacao|saude|"
    r"fornecedor(?:es)?|estados?|regioes|regiao|produtos?|itens?|clientes?|cidades?|localidades?|estoques?|armazens|armazem|depositos?)\b"
    r"(?: (?:do|da|de|dos|das) (lojas?|estabelecimentos?|filiais?|clientes?))?"
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
    "localidade": Dimension.LOCATION, "localidades": Dimension.LOCATION, "estoque": Dimension.LOCATION,
    "estoques": Dimension.LOCATION, "armazem": Dimension.LOCATION, "armazens": Dimension.LOCATION,
    "deposito": Dimension.LOCATION, "depositos": Dimension.LOCATION,
}  # fmt: skip

# "em quais estoques temos X?" / "onde temos X?": the places where a stock exists (positive values only).
_WHERE_LIST = re.compile(
    r"\bem (?:quais|que) (?:estoques?|localidades?|lojas?|estabelecimentos?|filiais?|armazens|armazem|depositos?)\b|"
    r"\bquais (?:estoques?|localidades?)\b|\bonde\b(?= (?:temos|tem|ha|havia|existem|existe|estao|esta|tinhamos)\b)"
)
# Stock concepts that are not values of an item ("qual o estoque de segurança?").
_STOCK_CONCEPT = re.compile(r"\bestoques? (?:de |do )?(?:seguranca|minimo|maximo|ideal|parado|obsoleto|excedente)\b")
# The only horizon a forecast is asked for: the official 3 months. Any other ("próximo mês",
# "próximos 6 meses") stays in the text and is refused as a relative period.
_HORIZON = re.compile(r"\b(?:nos |para os |durante os |pelos )?proximos (?:3|tres) meses\b")
# Direction words: "menos estoque". Only metrics that declare the opposite order accept them.
_ASCENDING = re.compile(r"\b(?:menos|menor(?:es)?|minim[oa]s?|pouc[oa]s?)\b")
# "now": for a metric with a history these words mean the current position.
_CURRENT = re.compile(r"\b(?:hoje|agora|atualmente|atual|atuais|no momento|neste momento|nesse momento)\b")

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
_DAY_TEXT = re.compile(r"\b(?:no dia |em |dia )?(\d{1,2})(?: de)? (" + "|".join(_MONTHS) + r")(?: de)? ((?:19|20)\d{2})\b")
_DAY_NUMERIC = re.compile(r"\b(?:no dia |em |dia )?(\d{1,2}) (\d{1,2}) ((?:19|20)\d{2})\b")
_DAY_ISO = re.compile(r"\b((?:19|20)\d{2})-(\d{2})-(\d{2})\b")
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
    r"\b(?:na|no|nas|nos|da|do|das|dos|em|para)\s+(?:loja|filial|estabelecimento|armazem|deposito|galpao|matriz|estado|"
    r"categoria|canal|regiao|cidade|segmento|vendedor|produto|item|sku|cliente)\b"
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

    # A single day ("15 de janeiro de 2026", "15/01/2026", "2026-01-15") is a one-day range.
    days: list[date] = []
    for pattern, order in ((_DAY_TEXT, "dmy_text"), (_DAY_NUMERIC, "dmy"), (_DAY_ISO, "ymd")):
        for match in pattern.finditer(working):
            try:
                if order == "dmy_text":
                    day = date(int(match.group(3)), _MONTHS[match.group(2)], int(match.group(1)))
                elif order == "dmy":
                    day = date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
                else:
                    day = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            except ValueError:
                return None, "the date does not exist", working
            days.append(day)
        working = pattern.sub(" ", working)
    ranges.extend(DateRange(day, day) for day in days)

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


def _is_whole_month(start: date, end: date) -> bool:
    return (start.year, start.month) == (end.year, end.month) and start.day == 1 and end.day == calendar.monthrange(end.year, end.month)[1]


def _strip_top_n(text: str) -> str:
    """Blank the number of "top 5" / "os 3 produtos" (the entity word stays)."""
    for pattern in _TOP_N_PATTERNS:
        text = pattern.sub(lambda m: m.group(0).replace(m.group(1), " ", 1), text)
    return _TOP_N_WORD.sub(lambda m: m.group(0).replace(m.group(1), " ", 1), text)


def _display_phrase(phrase: str | None, raw: str | None) -> str | None:
    """The phrase as the user typed it ("Loja Leão"), found by comparing words
    without accents or case; the normalized phrase when it cannot be found."""
    if phrase is None or not raw:
        return phrase
    words = raw.split()
    stream: list[str] = []
    owner: list[int] = []
    for index, word in enumerate(words):
        for token in normalize_text(word).split():
            stream.append(token)
            owner.append(index)
    target = normalize_text(phrase).split()
    for start in range(len(stream) - len(target) + 1):
        if stream[start : start + len(target)] == target:
            shown = " ".join(words[owner[start] : owner[start + len(target) - 1] + 1]).strip(" ?!.,;:")
            if shown and shown.isprintable() and len(shown) <= MAX_TEXT_LENGTH and normalize_text(shown) == normalize_text(phrase):
                return shown
    return phrase


def parse_analytical_request(text: str, raw: str | None = None) -> ParseOutcome:
    """Map the normalized data clause of a question to an approved request,
    a controlled refusal, or "not analytical". ``raw`` is the question as the
    user typed it: only used to show a name filter the way it was written."""
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
            if reason == _FORECAST_REFUSAL and metric is Metric.DEMAND_FORECAST_P85:
                continue  # the one forecast that is supported; its shape is checked below
            return _unsupported(reason)
    if metric is None:
        return _unsupported("ambiguous metric: say 'receita' (R$) or 'unidades vendidas'") if _AMBIGUOUS_SALES.search(t) else _NOT_ANALYTICAL
    definition = METRICS[metric]
    fixed = definition.fixed_shape  # a metric that can only be asked in one shape (a forecast series)
    has_cue = champion or fixed is not None or _any(_DATA_REQUEST, t)
    # A metric that takes name filters is asked in free wording ("quantos coletes temos?"):
    # every word of the question is accounted for (see ``qualifiers``).
    named = FilterKind.PRODUCT_TEXT in definition.filters

    # -- what is being asked ------------------------------------------------
    grain = _detect_grain(t)
    modifier = next((m for m, pattern in _MODIFIERS if pattern.search(t)), None)

    if named and not (has_cue or grain or modifier or _BY_PHRASE.search(t) or _WHERE_LIST.search(t)):
        return _NOT_ANALYTICAL  # a statement about stock ("explique o estoque de segurança"), not a request for values

    if named and _STOCK_CONCEPT.search(t):
        return _NOT_ANALYTICAL  # "estoque de segurança", "estoque mínimo": a concept, explained in the documentation

    where_list = _WHERE_LIST.search(t) if named else None
    has_cue = has_cue or bool(where_list)  # "onde temos X?" asks for values
    asked = _WHERE_LIST.sub(" ", t) if where_list else t  # the question without the "em quais estoques" part

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
                # Ambiguous only when the metric can be split by both kinds of state.
                states = [d for d in (Dimension.LOCATION_STATE, Dimension.CUSTOMER_STATE) if d in definition.dimensions]
                if len(states) != 1:
                    return _unsupported("'estado' is ambiguous: say 'estado do estabelecimento' or 'estado do cliente'")
                by_dimensions.append(states[0])
        elif head in _BY_DIMENSION:
            by_dimensions.append(_BY_DIMENSION[head])
        elif head not in ("mes", "dia", "trimestre", "ano"):
            return _unsupported(f"breakdown by '{head}' is not supported")

    # Remove what has been understood; whatever period/filter wording is left
    # is refused. (The metric's own words, e.g. "estoque atual", are not
    # period or filter wording.)
    if named:
        working = asked  # nothing is removed with the metric: its noun phrase may name the item asked about
    else:
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

    if fixed is not None:
        working = _HORIZON.sub(" ", working)
    date_range, refusal, working = _detect_period(working)
    if refusal:
        return _unsupported(refusal)

    # -- name filters and the position in time (metrics with a history) -----------
    qualifiers = None
    as_of: date | None = None
    if named:
        if fixed is not None and (date_range is not None or re.search(r"\bproximos?\b", working)):
            return _unsupported(_FORECAST_SHAPE_REFUSAL)  # only the official 3 months, never another period
        if date_range is not None:
            if date_range.start == date_range.end:
                as_of = date_range.start
            elif _is_whole_month(date_range.start, date_range.end):
                as_of = date_range.end  # a month means the position at its end
            else:
                return _unsupported(
                    "an inventory position needs one date or one month (e.g. '15 de janeiro de 2026', 'em janeiro de 2026'); "
                    "a year or a range is not supported"
                )
            date_range = None
        working = _CURRENT.sub(" ", working)  # "hoje", "atualmente": the current position
        qualifiers = extract_qualifiers(_strip_top_n(working))
        if qualifiers.refusal:
            return _unsupported(qualifiers.refusal)
        working = qualifiers.remaining
        if qualifiers.past_tense and as_of is None:
            return _unsupported("a past inventory position needs a date (e.g. 'em janeiro de 2026')")
        if fixed is not None and (qualifiers.product is None or qualifiers.location is None):
            return _unsupported(_FORECAST_SHAPE_REFUSAL)  # never widened to "all products" / "all stores"
        if metric is Metric.STOCK_ON_HAND and "estoque" not in t and "estoques" not in t and (
            qualifiers.product is None or qualifiers.product.split()[0] in _NOT_ITEMS
        ):
            return _NOT_ANALYTICAL  # "quantos ... temos" about something that is not an item's stock
    elif definition.family is MetricFamily.INVENTORY:
        item = item_after_quantity_word(metric_pattern.sub(" ", t) if metric_pattern is not None else t)
        if item is not None:
            return _unsupported(f"{metric.value} cannot be filtered by a named item ('{item}'); ask about stock, or drop the item")

    month_without_year = bool(_MONTH_NAME.search(working))
    relative_period = bool(_LEFTOVER_PERIOD.search(working))
    specific_filter = bool(_LEFTOVER_FILTER.search(working))
    # A metric with no question word ("Receita da loja X") is still a data
    # request when it carries scope wording: refuse it instead of leaving it
    # to documentation.
    scoped = bool(by_phrases or grain or modifier or date_range or as_of or month_without_year or relative_period or specific_filter)
    if not (has_cue or scoped):
        return _NOT_ANALYTICAL
    if month_without_year:
        return _unsupported("a month without a year is ambiguous")
    if relative_period:
        return _unsupported("relative or unrecognized periods are not supported: give a year (e.g. 'em 2025')")
    if specific_filter:
        return _unsupported("filtering by a specific store, state, category, product, channel or person is not supported")

    entities = [dim for pattern, dim in _ENTITY_DIMENSIONS if pattern.search(working)]
    ranking = champion or _any(_RANKING_CUES, asked)
    ascending = bool(_ASCENDING.search(asked))
    ranking_order: SortOrder | None = None
    if ascending and named and not ranking:
        return _unsupported("'menos'/'menor' only applies to a ranking (e.g. 'quais produtos têm menos estoque?')")
    if ascending and ranking and definition.ranking_order is SortOrder.DESC:
        if not definition.ranking_reverse_pt:
            return _unsupported('ascending rankings ("menos vendidos", lowest revenue) are not supported')
        ranking_order = SortOrder.ASC

    if fixed is not None and (grain or modifier or ranking or ascending or by_dimensions or where_list):
        return _unsupported(_FORECAST_SHAPE_REFUSAL)

    # -- build ---------------------------------------------------------------
    top_n: int | None = None
    time_grain: TimeGrain | None = None
    positive_only = False
    if fixed is not None:
        operation, dimensions = fixed[0], (fixed[1],)
    elif grain is not None:
        operation, dimensions, time_grain = Operation.TREND, (), grain
    elif where_list:
        if by_dimensions:
            return _unsupported("a list of places cannot also be broken down by another dimension")
        positive_only = True
        top_n = _extract_top_n(asked) if ranking else None
        operation, dimensions = (Operation.RANKING if top_n else Operation.BREAKDOWN), (Dimension.LOCATION,)
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
    if operation is not Operation.RANKING:
        ranking_order = None

    filters = RequestFilters(
        date_range=date_range,
        product_text=_display_phrase(qualifiers.product, raw) if qualifiers else None,
        category_text=_display_phrase(qualifiers.category, raw) if qualifiers else None,
        location_text=_display_phrase(qualifiers.location, raw) if qualifiers else None,
    )
    request = AnalyticalRequest(
        metric=metric,
        operation=operation,
        dimensions=dimensions,
        filters=filters,
        time_grain=time_grain,
        time_modifier=modifier,
        top_n=top_n,
        as_of=as_of,
        ranking_order=ranking_order,
        positive_only=positive_only,
    )
    try:
        check_compatibility(request)
    except UnsupportedRequestError as exc:
        return _unsupported(str(exc))
    return ParseOutcome(request=request)
