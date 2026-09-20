"""P85 demand forecast of ONE product in ONE store for the official 3 months.

Offline only. The eligibility rule (version 3, exactly the 3 official months, one
version, one ``generated_at``, no NULL, never a v1 fallback) is checked by running the
compiled statement on an in-memory SQLite that stands in for the table — it only
emulates the handful of Spark functions the statement uses — so the tests prove what
the rows the rule keeps and drops, not just how the SQL is spelled.

Products and stores are varied on purpose: nothing here is specific to one example.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date
from itertools import product as cartesian

import pytest

from src.harness.harness import AIHarness, HarnessStatus, SourceRoute
from src.harness.source_router import RuleBasedSourceRouter
from src.structured.analytical_request import AnalyticalRequest, RequestFilters, check_compatibility
from src.structured.compiler import compile_request
from src.structured.models import StructuredIntent, UnsupportedRequestError
from src.structured.rendering import no_data_message, render_structured_answer
from src.structured.semantic_model import FACTS, METRICS, Dimension, FilterKind, Metric, Operation
from src.structured.service import StructuredQueryService
from src.structured.text_match import SQL_ACCENTS_FROM, SQL_ACCENTS_TO

M = Metric.DEMAND_FORECAST_P85
ROUTER = RuleBasedSourceRouter()
FRIENDLY = "Não há previsão P85 disponível para esse produto nessa loja em todos os três meses solicitados."
GEN = "2026-09-18 15:11:51"  # the generation of the official run


def _request(product: str, location: str) -> AnalyticalRequest:
    return AnalyticalRequest(
        M, Operation.BREAKDOWN, (Dimension.FORECAST_MONTH,),
        RequestFilters(product_text=product, location_text=location),
    )


def _compile(request: AnalyticalRequest, catalog: str = "dev_catalog"):
    return compile_request(request, catalog=catalog, schema="gold")


# ---------------------------------------------------------------------------
# a stand-in table: SQLite plus the few Spark functions the statement uses
# ---------------------------------------------------------------------------


def _add_months(value: str, delta: int) -> str:
    year, month, day = (int(part) for part in value[:10].split("-"))
    index = year * 12 + (month - 1) + delta
    return date(index // 12, index % 12 + 1, day).isoformat()


def _fold(value: str | None) -> str | None:
    return None if value is None else value.translate(str.maketrans(SQL_ACCENTS_FROM, SQL_ACCENTS_TO))


def _row(product, location, month, value, *, version="3", generated_at=GEN):
    """One row of demand_forecast_rolling_3m (the columns of the real table)."""
    return (product, location, f"{month}-01T00:00:00", None if value is None else float(value), value, "CatBoost", version, generated_at)


def _series(product, location, values, *, months=("2026-09", "2026-10", "2026-11"), **kwargs):
    return [_row(product, location, month, value, **kwargs) for month, value in zip(months, values)]


def _run(request: AnalyticalRequest, rows: list[tuple]):
    query = _compile(request)
    connection = sqlite3.connect(":memory:")
    try:
        connection.create_function("add_months", 2, _add_months)
        connection.create_function("translate", 3, lambda value, *_: _fold(value))
        connection.create_function("lower", 1, lambda value: None if value is None else value.lower())
        connection.execute(
            "CREATE TABLE demand_forecast_rolling_3m (product_name TEXT, location_name TEXT, month_start TEXT, demand_forcast REAL,"
            " unit_demand_forcast INTEGER, model_name TEXT, model_version TEXT, generated_at TEXT)"
        )
        connection.executemany("INSERT INTO demand_forecast_rolling_3m VALUES (?,?,?,?,?,?,?,?)", rows)
        connection.row_factory = sqlite3.Row
        sql = query.statement.replace("prod_lh_nautical.gold.", "")
        sql = re.sub(r"CAST\(((?:[^()]|\([^()]*\))+) AS DATE\)", r"date(\1)", sql)
        return [dict(row) for row in connection.execute(sql, dict(query.parameters))]
    finally:
        connection.close()


LEAO = "Loja Leão"
COLETE = "Colete Salva-Vidas 2374"


def _official(*extra):
    """The official run: the Colete in Loja Leão, complete and clean, plus whatever ``extra`` adds."""
    return _series(COLETE, LEAO, (9, 10, 12)) + list(extra)


# ---------------------------------------------------------------------------
# 1. acceptance
# ---------------------------------------------------------------------------


def test_an_eligible_series_returns_exactly_its_three_official_months():
    rows = _run(_request(COLETE, LEAO), _official())

    assert [(r["forecast_month"], r["product_name"], r["location_name"], r["demand_forecast_p85"]) for r in rows] == [
        ("2026-09-01", COLETE, LEAO, 9),
        ("2026-10-01", COLETE, LEAO, 10),
        ("2026-11-01", COLETE, LEAO, 12),
    ]


@pytest.mark.parametrize(
    "product, location, values",
    [
        ("Bateria Náutica 5523", "Armazém Camargo", (4, 6, 7)),
        ("GPS Plotter 2111", "Loja Oliveira", (1, 1, 2)),
        ("Hélice de Alumínio 549", "Armazém Siqueira", (0, 3, 5)),  # a zero from the official run is a value, not a hole
    ],
)
def test_it_is_product_x_store_not_one_hardcoded_example(product, location, values):
    table = _official() + _series(product, location, values) + _series("Outro Produto 1", "Loja Mendonça", (7, 7, 7))

    rows = _run(_request(product, location), table)

    assert [r["demand_forecast_p85"] for r in rows] == list(values)
    assert {(r["product_name"], r["location_name"]) for r in rows} == {(product, location)}


def test_lowercase_text_without_accents_finds_the_same_series():
    rows = _run(_request("colete salva vidas 2374", "loja leao"), _official())

    assert len(rows) == 3


def test_rows_outside_the_window_do_not_disturb_an_eligible_series():
    older = _series(COLETE, LEAO, (5, 6, 7), months=("2026-05", "2026-06", "2026-07"))  # the model's past-month rows

    assert len(_run(_request(COLETE, LEAO), _official(*older))) == 3


# ---------------------------------------------------------------------------
# 2. the eligibility rule: what must be refused (zero rows)
# ---------------------------------------------------------------------------


def _stale_v1_zeros(product=COLETE, location=LEAO):
    return _series(product, location, (0, 0, 0), version="1", generated_at="2026-09-18 12:03:31")


def test_a_series_that_only_has_v1_rows_is_not_eligible_and_v1_is_no_fallback():
    # the official run exists for OTHER series (so the window is 2026-09..11); this series only has stale v1 zeros
    table = _series("Outro Produto 1", "Loja Mendonça", (3, 4, 5)) + _stale_v1_zeros()

    assert _run(_request(COLETE, LEAO), table) == []


def test_a_series_mixing_versions_in_the_window_is_not_eligible():
    mixed = _series(COLETE, LEAO, (9, 10), months=("2026-09", "2026-10")) + [_row(COLETE, LEAO, "2026-11", 0, version="1", generated_at="2026-09-18 12:03:31")]
    table = _series("Outro Produto 1", "Loja Mendonça", (3, 4, 5)) + mixed

    assert _run(_request(COLETE, LEAO), table) == []


@pytest.mark.parametrize(
    "months",
    [
        ("2026-10", "2026-11"),  # only 2 of the 3 official months
        ("2026-09", "2026-10"),
        ("2026-08", "2026-09", "2026-10"),  # a horizon anchored on the series' own last sale: 2 official + 1 past
        ("2026-05", "2026-06", "2026-07"),  # entirely in the past (a dormant series)
    ],
)
def test_a_series_without_exactly_the_three_official_months_is_not_eligible(months):
    table = _series("Outro Produto 1", "Loja Mendonça", (3, 4, 5)) + _series(COLETE, LEAO, (9, 10, 12)[: len(months)], months=months)

    assert _run(_request(COLETE, LEAO), table) == []


def test_a_series_with_more_than_one_generated_at_is_not_eligible():
    rows = _series(COLETE, LEAO, (9, 10), months=("2026-09", "2026-10")) + [_row(COLETE, LEAO, "2026-11", 12, generated_at="2026-09-18 15:11:52")]

    assert _run(_request(COLETE, LEAO), rows + _series("Outro Produto 1", "Loja Mendonça", (3, 4, 5))) == []


def test_a_series_with_a_null_forecast_is_not_eligible():
    rows = _series(COLETE, LEAO, (9, None, 12)) + _series("Outro Produto 1", "Loja Mendonça", (3, 4, 5))

    assert _run(_request(COLETE, LEAO), rows) == []


def test_an_unknown_product_or_store_returns_nothing():
    assert _run(_request("Produto Que Não Existe 999", LEAO), _official()) == []
    assert _run(_request(COLETE, "Loja Inexistente"), _official()) == []


def test_ambiguity_is_refused_never_summed_or_guessed():
    table = _official() + _series("Colete Salva-Vidas 5603", LEAO, (1, 2, 3)) + _series(COLETE, "Loja Oliveira", (4, 5, 6))

    assert _run(_request("Colete Salva-Vidas", LEAO), table) == []  # two products match (both eligible)
    assert _run(_request(COLETE, "Loja"), table) == []  # two stores match
    assert len(_run(_request(COLETE, LEAO), table)) == 3  # the exact product and store still work


def test_the_official_window_comes_from_the_v3_rows_not_from_other_versions_or_the_clock():
    # v3 ends in December: the window is Oct..Dec, whatever else the table holds
    december = _series("Produto Dezembro 1", LEAO, (2, 3, 4), months=("2026-10", "2026-11", "2026-12"))
    stale_later_v1 = [_row("Produto Dezembro 1", "Loja Oliveira", "2027-03", 9, version="1")]
    request = _request("Produto Dezembro 1", LEAO)

    rows = _run(request, december + stale_later_v1)

    assert [r["forecast_month"] for r in rows] == ["2026-10-01", "2026-11-01", "2026-12-01"]
    # ...and a series that only covers Sep..Nov is then out of the window
    assert _run(_request(COLETE, LEAO), december + _official()) == []


# ---------------------------------------------------------------------------
# 3. the compiled statement
# ---------------------------------------------------------------------------


def test_the_catalog_schema_and_table_come_only_from_the_registry():
    fact = FACTS["demand_forecast"]
    for configured in ("dev_natalia_correia_lh_nautical", "ci_lh_nautical", "anything"):
        sql = _compile(_request(COLETE, LEAO), catalog=configured).statement
        assert f"{fact.catalog}.{fact.schema}.{fact.table}" in sql
        assert configured not in sql
    # every other fact keeps using the App's DATA_CATALOG
    stock = compile_request(
        AnalyticalRequest(Metric.STOCK_ON_HAND, Operation.AGGREGATE), catalog="dev_natalia_correia_lh_nautical", schema="gold"
    )
    assert "dev_natalia_correia_lh_nautical.gold.fct_current_inventory" in stock.statement and "prod_lh_nautical" not in stock.statement


def test_only_the_forecast_fact_names_its_own_catalog():
    assert {key for key, fact in FACTS.items() if fact.catalog} == {"demand_forecast"}


def test_the_statement_encodes_the_rule_with_named_parameters_only():
    query = _compile(_request(COLETE, LEAO))
    sql = re.sub(r"\s+", " ", query.statement)

    assert "CAST(MAX(f.month_start) AS DATE) AS window_end" in sql and "WHERE f.model_version = :official_version" in sql
    assert "BETWEEN add_months(b.window_end, -2) AND b.window_end" in sql
    for clause in (
        "HAVING COUNT(*) = 3",
        "COUNT(DISTINCT CAST(f.month_start AS DATE)) = 3",
        "COUNT(CASE WHEN f.model_version = :official_version THEN 1 END) = 3",
        "COUNT(DISTINCT f.generated_at) = 1",
        "COUNT(f.unit_demand_forcast) = 3",
        "(SELECT COUNT(*) FROM matched) = 1",
        "(f.product_name, f.location_name) IN (SELECT product_name, location_name FROM eligible)",
    ):
        assert clause in sql, clause
    assert query.parameters["official_version"] == "3"
    assert not re.search(r"model_version\s*(=|IN|<>|!=)\s*'", sql) and "'1'" not in sql  # no version literal, no v1 anywhere
    assert set(re.findall(r":([a-z_0-9]+)", sql)) == set(query.parameters)
    assert query.source_tables == ("gold.demand_forecast_rolling_3m",)


@pytest.mark.parametrize("hostile", ["x'; DROP TABLE t; --", "100%_", "colete) UNION SELECT 1", 'a" OR 1=1 --'])
def test_user_text_never_reaches_the_statement(hostile):
    query = _compile(_request(hostile, hostile))

    for fragment in ("DROP", "UNION", "OR 1=1", hostile):
        assert fragment not in query.statement, fragment
    for key, value in query.parameters.items():
        assert key == "official_version" or re.fullmatch(r"%[a-z0-9]+%", value), (key, value)


# ---------------------------------------------------------------------------
# 4. the request: one product, one store, this shape only
# ---------------------------------------------------------------------------


def test_the_metric_is_registered_as_a_p85_forecast_and_never_as_expected_demand():
    definition = METRICS[M]

    assert definition.required_filters == {FilterKind.PRODUCT_TEXT, FilterKind.LOCATION_TEXT}
    assert definition.operations == {Operation.BREAKDOWN} and definition.dimensions == {Dimension.FORECAST_MONTH}
    assert "P85" in definition.label_pt and "percentil 85" in definition.display_pt
    text = " ".join((definition.label_pt, definition.display_pt, definition.sentence_pt, definition.ranking_pt, definition.note_pt))
    for forbidden in ("esperad", "nível de serviço", "nivel de servico", "expected"):
        assert forbidden not in text.lower().replace("não representa a demanda média esperada", ""), forbidden


@pytest.mark.parametrize(
    "request_",
    [
        AnalyticalRequest(M, Operation.BREAKDOWN, (Dimension.FORECAST_MONTH,)),  # no product, no store
        AnalyticalRequest(M, Operation.BREAKDOWN, (Dimension.FORECAST_MONTH,), RequestFilters(product_text=COLETE)),
        AnalyticalRequest(M, Operation.BREAKDOWN, (Dimension.FORECAST_MONTH,), RequestFilters(location_text=LEAO)),
        AnalyticalRequest(M, Operation.AGGREGATE, (), RequestFilters(product_text=COLETE, location_text=LEAO)),  # a quarterly total
        AnalyticalRequest(M, Operation.RANKING, (Dimension.PRODUCT,), RequestFilters(location_text=LEAO)),
        AnalyticalRequest(M, Operation.BREAKDOWN, (Dimension.FORECAST_MONTH,), RequestFilters(product_text=COLETE, location_text=LEAO, category_text="X")),
    ],
)
def test_anything_but_one_product_in_one_store_month_by_month_is_refused(request_):
    with pytest.raises(UnsupportedRequestError):
        check_compatibility(request_)


# ---------------------------------------------------------------------------
# 5. natural language
# ---------------------------------------------------------------------------


def _decide(question: str):
    return ROUTER.route(question)


@pytest.mark.parametrize(
    "question, product, location",
    [
        ("Qual a previsão do Colete Salva-Vidas 2374 na Loja Leão nos próximos 3 meses?", COLETE, LEAO),
        ("Qual a previsão de demanda da Bateria Náutica 5523 no Armazém Camargo?", "Bateria Náutica 5523", "Armazém Camargo"),
        ("Previsão do GPS Plotter 2111 na Loja Oliveira para os próximos três meses", "GPS Plotter 2111", "Loja Oliveira"),
        ("qual a previsao da helice de aluminio 549 no armazem siqueira nos proximos 3 meses", "helice de aluminio 549", "armazem siqueira"),
    ],
)
def test_natural_questions_become_a_one_product_one_store_forecast(question, product, location):
    decision = _decide(question)
    request = decision.structured_params["request"]

    assert decision.route is SourceRoute.STRUCTURED_DATA
    assert (request.metric, request.operation, request.dimensions) == (M, Operation.BREAKDOWN, (Dimension.FORECAST_MONTH,))
    assert (request.filters.product_text, request.filters.location_text) == (product, location)  # nothing dropped


@pytest.mark.parametrize(
    "question",
    [
        "Qual a previsão de demanda?",  # nothing named
        "Qual a previsão do Colete Salva-Vidas 2374?",  # no store
        "Qual a previsão da Loja Leão nos próximos 3 meses?",  # no product
        "Qual a previsão do Colete Salva-Vidas 2374 em todas as lojas?",
        "Qual a previsão do Colete Salva-Vidas 2374 na Loja Leão nos próximos 6 meses?",  # another horizon
        "Qual a previsão do Colete Salva-Vidas 2374 na Loja Leão no próximo mês?",
        "Qual a previsão do Colete Salva-Vidas 2374 na Loja Leão em novembro de 2026?",  # a period is not asked
        "Qual a previsão do Colete Salva-Vidas 2374 na Loja Leão por mês?",
        "Quais produtos terão maior demanda na Loja Leão nos próximos 3 meses?",  # a ranking
        "Qual a previsão de receita da Loja Leão nos próximos 3 meses?",  # a forecast of something else
        "Qual a previsão do Colete Salva-Vidas 2374 e da Bateria Náutica 5523 na Loja Leão?",  # two products
    ],
)
def test_everything_else_is_refused_not_widened_and_not_sent_to_the_documentation(question):
    decision = _decide(question)

    assert decision.route is SourceRoute.STRUCTURED_DATA and decision.unsupported_reason and not decision.structured_params


def test_a_question_about_how_the_forecast_works_still_goes_to_the_documentation():
    assert _decide("Como é calculada a previsão de demanda?").route is SourceRoute.AI_SEARCH


# ---------------------------------------------------------------------------
# 6. the answer the user reads
# ---------------------------------------------------------------------------


class FakeExecutor:
    def __init__(self, rows=()):
        self.rows, self.calls = list(rows), []

    def execute(self, statement, parameters=None):
        self.calls.append((statement, dict(parameters or {})))
        return [dict(row) for row in self.rows]


def _harness(rows=()):
    executor = FakeExecutor(rows)
    harness = AIHarness(
        orchestrator=None,
        structured_query_service=StructuredQueryService(executor, catalog="dev_catalog"),
        source_router=ROUTER,
    )
    return harness, executor


def _api_rows(product=COLETE, location=LEAO, values=(9, 10, 12)):
    months = ("2026-09-01", "2026-10-01", "2026-11-01")
    return [
        {"forecast_month": month, "product_name": product, "location_name": location, "demand_forecast_p85": str(value)}
        for month, value in zip(months, values)
    ]


def test_the_answer_says_p85_shows_each_month_and_carries_the_caveat():
    harness, executor = _harness(_api_rows())
    result = harness.ask("Qual a previsão do Colete Salva-Vidas 2374 na Loja Leão nos próximos 3 meses?")

    assert result.status is HarnessStatus.STRUCTURED_DATA_OK
    assert result.answer == (
        "Previsão quantílica P85 (percentil 85) para Colete Salva-Vidas 2374 em Loja Leão, de setembro a novembro de 2026:\n\n"
        "- set/2026 — 9 unidades\n- out/2026 — 10 unidades\n- nov/2026 — 12 unidades\n\n"
        "É um cenário mais conservador para planejamento de estoque e não representa a demanda média esperada.\n"
        "Fonte: camada Gold — demand_forecast_rolling_3m."
    )
    assert "prod_lh_nautical" not in result.answer and "dev_catalog" not in result.answer  # no catalog or schema shown
    assert "nível de serviço" not in result.answer.lower()
    assert "[S" not in result.answer and result.rag_result is None  # structured only: the API keeps sources = []
    assert len(executor.calls) == 1


def test_singular_and_zero_units_read_naturally():
    harness, _ = _harness(_api_rows("GPS Plotter 2111", "Loja Oliveira", (1, 0, 2)))
    answer = harness.ask("Qual a previsão do GPS Plotter 2111 na Loja Oliveira?").answer

    assert "- set/2026 — 1 unidade\n- out/2026 — 0 unidades\n- nov/2026 — 2 unidades" in answer
    assert answer.startswith("Previsão quantílica P85 (percentil 85) para GPS Plotter 2111 em Loja Oliveira")


def test_a_result_with_more_than_one_series_is_never_shown():
    rows = _api_rows() + _api_rows("Bateria Náutica 5523", "Loja Leão")
    harness, _ = _harness(rows)

    assert harness.ask("Qual a previsão do Colete Salva-Vidas 2374 na Loja Leão?").status is HarnessStatus.STRUCTURED_DATA_FAILED


def test_an_ineligible_series_gets_the_friendly_message_with_the_same_status():
    harness, executor = _harness([])
    result = harness.ask("Qual a previsão do Colete Salva-Vidas 2374 na Loja Leão nos próximos 3 meses?")

    assert result.status is HarnessStatus.STRUCTURED_DATA_NO_DATA and not result.answer
    assert no_data_message(result.structured_result) == FRIENDLY
    assert len(executor.calls) == 1


def test_other_metrics_keep_the_generic_no_data_message():
    harness, _ = _harness([])
    result = harness.ask("Qual o faturamento em 2025?")

    assert result.status is HarnessStatus.STRUCTURED_DATA_NO_DATA and no_data_message(result.structured_result) is None
    assert no_data_message(None) is None


def test_the_forecast_never_falls_back_to_the_documentation():
    harness, executor = _harness([])
    result = harness.ask("Qual a previsão do Colete Salva-Vidas 2374 na Loja Leão nos próximos 3 meses?")

    assert result.rag_result is None and result.source_route is SourceRoute.STRUCTURED_DATA
    assert result.status is not HarnessStatus.AI_SEARCH_OK


def test_a_refused_forecast_question_executes_nothing():
    harness, executor = _harness(_api_rows())

    for question in ("Qual a previsão de demanda?", "Qual a previsão do Colete Salva-Vidas 2374 em todas as lojas?"):
        assert harness.ask(question).status is HarnessStatus.STRUCTURED_DATA_UNSUPPORTED
    assert executor.calls == []


def test_render_is_deterministic_from_the_registry_wording():
    service = StructuredQueryService(FakeExecutor(_api_rows("Âncora Bruce 2416", "Armazém Novaes", (5, 8, 9))), catalog="c")
    result = service.execute(StructuredIntent.ANALYTICS, {"request": _request("Âncora Bruce 2416", "Armazém Novaes")})
    text = render_structured_answer(result)

    assert "para Âncora Bruce 2416 em Armazém Novaes, de setembro a novembro de 2026:" in text
    assert text.count("\n- ") == 3 and METRICS[M].note_pt in text


# ---------------------------------------------------------------------------
# 7. existing behaviour is untouched
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question, metric",
    [
        ("Qual o faturamento em 2025?", Metric.NET_REVENUE),
        ("Quais os 5 produtos mais vendidos em 2025?", Metric.TOTAL_QUANTITY_SOLD),
        ("Quantos coletes temos em estoque?", Metric.STOCK_ON_HAND),
        ("Qual o estoque do Colete Salva-Vidas 2374 na Loja Leão?", Metric.STOCK_ON_HAND),
        ("Quais produtos têm mais risco de ruptura?", Metric.STOCKOUT_RISK),
        ("Quantos clientes temos?", Metric.CUSTOMER_COUNT),
    ],
)
def test_existing_questions_still_parse_to_their_own_metric(question, metric):
    assert _decide(question).structured_params["request"].metric is metric


@pytest.mark.parametrize("question", ["Qual a previsão de receita?", "Qual a previsão de vendas do mês que vem?", "Qual o lucro previsto?"])
def test_forecasts_of_other_things_stay_refused(question):
    assert _decide(question).route is SourceRoute.STRUCTURED_DATA and _decide(question).unsupported_reason


def test_other_facts_compile_exactly_as_before():
    revenue = compile_request(AnalyticalRequest(Metric.NET_REVENUE, Operation.AGGREGATE), catalog="dev_catalog", schema="gold")

    assert "FROM dev_catalog.gold.fct_sales AS s" in revenue.statement and "bounds" not in revenue.statement
    assert "translate(" not in revenue.statement


def test_the_forecast_never_touches_the_dimension_joins_of_other_facts():
    sql = _compile(_request(COLETE, LEAO)).statement

    assert "dim_products" not in sql and "dim_locations" not in sql and "fct_" not in sql


def test_every_store_and_product_pair_of_a_grid_gets_its_own_parameters():
    seen = set()
    for product, location in cartesian(("Bateria Náutica 5523", "GPS Plotter 2111"), ("Loja Leão", "Armazém Camargo")):
        query = _compile(_request(product, location))
        seen.add(tuple(sorted(v for k, v in query.parameters.items() if k != "official_version")))
    assert len(seen) == 4
