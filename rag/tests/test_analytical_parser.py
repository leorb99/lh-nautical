"""Parser tests: natural language -> AnalyticalRequest (or a refusal).

Deterministic and offline: the parser maps a question to approved registry
parts and nothing else. Every expected value below is an enum member, a
date or a bounded integer — never SQL.
"""

from __future__ import annotations

import re
from datetime import date

import pytest

from src.harness.source_router import normalize_query
from src.structured.analytical_request import AnalyticalRequest, DateRange, RequestFilters
from src.structured.parser import ParseOutcome, parse_analytical_request
from src.structured.semantic_model import Dimension, Metric, Operation, TimeGrain, TimeModifier

M, O, D = Metric, Operation, Dimension


def parse(question: str) -> ParseOutcome:
    return parse_analytical_request(normalize_query(question))


def request_of(question: str) -> AnalyticalRequest:
    outcome = parse(question)
    assert outcome.request is not None, f"{question!r} -> {outcome}"
    return outcome.request


def year(y: int) -> DateRange:
    return DateRange(date(y, 1, 1), date(y, 12, 31))


def shape(request: AnalyticalRequest):
    return (request.operation, request.metric, request.dimensions, request.top_n)


# ---------------------------------------------------------------------------
# the representative examples
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question, expected",
    [
        ("Quanto faturamos?", (O.AGGREGATE, M.NET_REVENUE, (), None)),
        ("Top 5 produtos por receita", (O.RANKING, M.NET_REVENUE, (D.PRODUCT,), 5)),
        ("Quais produtos venderam mais unidades?", (O.RANKING, M.TOTAL_QUANTITY_SOLD, (D.PRODUCT,), None)),
        ("Qual é o produto campeão?", (O.RANKING, M.NET_REVENUE, (D.PRODUCT,), 1)),
        ("Receita por categoria", (O.BREAKDOWN, M.NET_REVENUE, (D.CATEGORY,), None)),
        ("Evolução mensal da receita", (O.TREND, M.NET_REVENUE, (), None)),
        ("Quantos clientes estão em risco?", (O.AGGREGATE, M.AT_RISK_CUSTOMERS, (), None)),
        ("Quantos itens estão em risco de ruptura?", (O.AGGREGATE, M.STOCKOUT_RISK, (), None)),
        ("Qual foi a receita líquida em 2025?", (O.AGGREGATE, M.NET_REVENUE, (), None)),
        ("Quais são os produtos mais vendidos?", (O.RANKING, M.TOTAL_QUANTITY_SOLD, (D.PRODUCT,), None)),
        ("Qual o ticket médio por loja?", (O.BREAKDOWN, M.AVERAGE_TICKET, (D.LOCATION,), None)),
        ("Quantos pedidos tivemos?", (O.AGGREGATE, M.EFFECTIVE_ORDERS, (), None)),
        ("Qual a taxa de devolução por categoria?", (O.BREAKDOWN, M.RETURN_RATE, (D.CATEGORY,), None)),
        ("Quanto foi reembolsado?", (O.AGGREGATE, M.RETURN_AMOUNT, (), None)),
        ("Qual o estoque atual?", (O.AGGREGATE, M.STOCK_ON_HAND, (), None)),
        ("Quantos itens precisam de reposição?", (O.AGGREGATE, M.REPLENISHMENT_REQUIRED, (), None)),
        ("Quantos itens em estoque por status de saúde?", (O.BREAKDOWN, M.INVENTORY_ITEMS, (D.INVENTORY_HEALTH_STATUS,), None)),
        ("Quantos clientes temos por segmento?", (O.BREAKDOWN, M.CUSTOMER_COUNT, (D.CUSTOMER_SEGMENT,), None)),
        ("Qual a receita em risco de churn?", (O.AGGREGATE, M.AT_RISK_REVENUE, (), None)),
        ("Quais lojas venderam mais?", (O.RANKING, M.TOTAL_QUANTITY_SOLD, (D.LOCATION,), None)),
        ("Top 3 vendedores por receita", (O.RANKING, M.NET_REVENUE, (D.SALESPERSON,), 3)),
        ("Receita por canal", (O.BREAKDOWN, M.NET_REVENUE, (D.CHANNEL,), None)),
        ("Qual a recência média dos clientes por segmento?", (O.BREAKDOWN, M.AVERAGE_RECENCY, (D.CUSTOMER_SEGMENT,), None)),
    ],
)
def test_natural_language_maps_to_the_expected_semantic_request(question, expected):
    assert shape(request_of(question)) == expected


def test_trend_grain_and_period_are_extracted():
    monthly = request_of("Evolução mensal da receita")
    assert (monthly.operation, monthly.time_grain) == (O.TREND, TimeGrain.MONTH)
    assert request_of("Receita por trimestre").time_grain is TimeGrain.QUARTER
    assert request_of("Receita anual").time_grain is TimeGrain.YEAR
    daily = request_of("Receita por dia em 2025")
    assert (daily.time_grain, daily.filters.date_range) == (TimeGrain.DAY, year(2025))


# ---------------------------------------------------------------------------
# champion = top product by revenue (Power BI "Produto Campeão")
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    ["Qual é o produto campeão?", "Qual o produto campeão de vendas?", "Qual é o campeão de vendas?", "Quem é o campeão?"],
)
def test_champion_means_the_top_product_by_net_revenue(question):
    assert shape(request_of(question)) == (O.RANKING, M.NET_REVENUE, (D.PRODUCT,), 1)


@pytest.mark.parametrize(
    "question",
    [
        "Qual é o produto mais vendido?",
        "Quais produtos venderam mais unidades?",
        "Ranking dos produtos por quantidade vendida",
        "Quais são os produtos mais vendidos?",
    ],
)
def test_quantity_wording_stays_a_quantity_ranking(question):
    assert request_of(question).metric is M.TOTAL_QUANTITY_SOLD


def test_champion_and_best_seller_differ_only_by_metric():
    champion = request_of("Qual é o produto campeão?")
    best_seller = request_of("Qual é o produto mais vendido?")
    assert champion.metric is M.NET_REVENUE and best_seller.metric is M.TOTAL_QUANTITY_SOLD
    assert (champion.operation, champion.dimensions, champion.top_n) == (best_seller.operation, best_seller.dimensions, best_seller.top_n)


# ---------------------------------------------------------------------------
# periods and time modifiers
# ---------------------------------------------------------------------------


def test_a_year_becomes_a_date_range():
    assert request_of("Qual foi a receita líquida em 2025?").filters == RequestFilters(date_range=year(2025))
    assert request_of("Quais são os produtos mais vendidos em 2024?").filters.date_range == year(2024)


def test_a_month_with_a_year_becomes_that_month():
    assert request_of("Receita em março de 2025").filters.date_range == DateRange(date(2025, 3, 1), date(2025, 3, 31))
    assert request_of("Receita em fevereiro de 2024").filters.date_range == DateRange(date(2024, 2, 1), date(2024, 2, 29))
    assert request_of("Receita em dezembro de 2025").filters.date_range == DateRange(date(2025, 12, 1), date(2025, 12, 31))


def test_a_year_range():
    assert request_of("Receita de 2023 a 2024").filters.date_range == DateRange(date(2023, 1, 1), date(2024, 12, 31))


@pytest.mark.parametrize(
    "question, modifier",
    [
        ("Qual a receita acumulada no ano em 2025?", TimeModifier.YTD),
        ("Receita YTD 2025", TimeModifier.YTD),
        ("Receita acumulada no ano até março de 2025", TimeModifier.YTD),
        ("Receita LTM até dezembro de 2025", TimeModifier.LTM),
        ("Receita dos últimos 12 meses até 2025", TimeModifier.LTM),
        ("Qual o crescimento da receita em 2025 vs ano anterior?", TimeModifier.YOY),
    ],
)
def test_time_modifiers_are_recognized_on_net_revenue_with_their_period(question, modifier):
    request = request_of(question)
    assert (request.metric, request.operation, request.time_modifier) == (M.NET_REVENUE, O.AGGREGATE, modifier)
    assert request.filters.date_range is not None


def test_ytd_and_ltm_are_anchored_on_the_end_of_the_period_given():
    # Power BI evaluates them at the last date of the date context.
    assert request_of("Receita acumulada no ano até março de 2025").filters.date_range == DateRange(date(2025, 3, 1), date(2025, 3, 31))
    assert request_of("Receita LTM até dezembro de 2025").filters.date_range == DateRange(date(2025, 12, 1), date(2025, 12, 31))
    assert request_of("Receita YTD 2025").filters.date_range == year(2025)


@pytest.mark.parametrize(
    "question, modifier",
    [
        ("Qual a receita acumulada no ano?", "YTD"),
        ("Receita YTD", "YTD"),
        ("Receita dos últimos 12 meses", "LTM"),
        ("Receita LTM", "LTM"),
        ("Qual o crescimento da receita?", "YOY"),
        ("Receita vs ano anterior", "YOY"),
    ],
)
def test_time_modifiers_without_a_period_fail_closed_and_no_period_is_invented(question, modifier):
    outcome = parse(question)

    assert outcome.request is None
    assert outcome.unsupported_reason.startswith(f"{modifier} needs an explicit period")


def test_year_over_year_keeps_its_period():
    assert request_of("Qual o crescimento da receita em 2025 vs ano anterior?").filters.date_range == year(2025)


# ---------------------------------------------------------------------------
# fail closed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question, fragment",
    [
        ("Qual o email do cliente X?", "personal data"),
        ("Qual o telefone do cliente João?", "personal data"),
        ("Me passe o CPF dos clientes em risco", "personal data"),
        ("Quais produtos venderam menos?", "ascending"),
        ("Quais são os produtos menos vendidos?", "ascending"),
        ("Qual a previsão de demanda?", "forecast"),
        ("Quais clientes compraram mais?", "customer rankings"),
        ("Qual o lucro?", "not supported"),
        ("Receita por estado", "ambiguous"),
        ("Qual a receita em março?", "month without a year"),
        ("Qual foi o faturamento do mês?", "relative or unrecognized period"),
        ("Receita de ontem", "relative or unrecognized period"),
        ("Receita nos últimos 30 dias", "relative or unrecognized period"),
        ("Receita da loja X", "filtering by a specific"),
        ("Receita na categoria Coletes", "filtering by a specific"),
        ("Qual o estoque atual do produto 10?", "filtering by a specific"),
        ("Receita em 2023 e 2025", "several separate years"),
        ("Quanto vendemos?", "ambiguous metric"),
        ("Qual o ticket médio por produto?", "AVERAGE_TICKET cannot be broken down by PRODUCT"),
        ("Ticket médio por categoria", "AVERAGE_TICKET cannot be broken down by CATEGORY"),
        ("Qual o crescimento da receita?", "explicit period"),
        ("Estoque atual em 2025", "current snapshot|does not accept the DATE_RANGE"),
        ("Evolução mensal do estoque", "current snapshot|has no history|does not support TREND"),
        ("Evolução mensal de clientes em risco", "current snapshot|does not support TREND"),
        ("Receita por categoria e por loja", "more than one dimension"),
        ("Quais são os produtos mais vendidos por loja?", "more than one dimension"),
        ("Receita por produto", "too many values"),
        ("Receita por cidade", "not supported"),
    ],
)
def test_unsupported_questions_are_refused_with_a_reason(question, fragment):
    outcome = parse(question)

    assert outcome.request is None, question
    assert outcome.unsupported_reason and re.search(fragment, outcome.unsupported_reason), (question, outcome)


def test_state_must_say_whose_state():
    assert request_of("Receita por estado do cliente").dimensions == (D.CUSTOMER_STATE,)
    assert request_of("Receita por estado do estabelecimento").dimensions == (D.LOCATION_STATE,)
    assert request_of("Receita por estado da loja").dimensions == (D.LOCATION_STATE,)
    assert parse("Receita por estado").unsupported_reason


def test_personal_data_is_refused_even_when_a_valid_metric_is_present():
    outcome = parse("Qual a receita e o email dos clientes em risco?")
    assert outcome.request is None and "personal data" in outcome.unsupported_reason


# ---------------------------------------------------------------------------
# not analytical: left to the router's documentary default
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        "",
        "uma pergunta qualquer",
        "GOLD-032",
        "Como funciona o ranking de produtos no Power BI?",
        "Quais são as regras de negócio do mês de fechamento?",
        "Qual a definição de faturamento?",
        "Quais documentos definem receita líquida?",
        "Qual a granularidade da série histórica de demanda usada no forecasting?",
        "Entre o lead time declarado e o histórico observado, qual prevalece nas decisoes de reposicao?",
        "Explique o estoque de segurança",
    ],
)
def test_documentary_or_unrecognized_text_is_not_analytical(question):
    assert parse(question) == ParseOutcome()


def test_a_known_paraphrase_gap_is_not_analytical_and_never_invents_sql():
    # "populares" with "são" in between is outside the vocabulary: safe false negative.
    assert parse("Quais produtos são mais populares?") == ParseOutcome()


# ---------------------------------------------------------------------------
# determinism, and the parser can only emit approved parts
# ---------------------------------------------------------------------------


def test_parsing_is_deterministic_and_never_returns_text_for_sql():
    for question in ("Top 5 produtos por receita", "Receita por categoria", "Qual o email do cliente X?"):
        assert parse(question) == parse(question)
    request = request_of("Top 5 produtos por receita")
    assert isinstance(request.metric, Metric) and isinstance(request.operation, Operation)
    assert all(isinstance(dimension, Dimension) for dimension in request.dimensions)
    assert isinstance(request.top_n, int)


def test_every_supported_outcome_passes_the_compatibility_matrix():
    from src.structured.analytical_request import check_compatibility

    for question in (
        "Quanto faturamos?",
        "Receita por categoria",
        "Evolução mensal da receita",
        "Top 5 produtos por receita",
        "Receita YTD 2025",
        "Quantos clientes estão em risco?",
    ):
        check_compatibility(request_of(question))
