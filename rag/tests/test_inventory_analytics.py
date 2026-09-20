"""Inventory analytics: current stock, stock as of a past date, name filters.

Offline only (fake SQL executor). Covers the whole chain the extension touches:
question -> router/parser -> ``AnalyticalRequest`` -> compiler -> service ->
deterministic renderer, and the guarantee that a qualifier of the question
(product, category, location, date) is either represented in the request or the
question is refused — it never disappears.

Names of products and places are deliberately varied: nothing here (or in the
implementation) is specific to one example.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal

import pytest

from src.harness.harness import AIHarness, HarnessStatus, SourceRoute
from src.harness.source_router import RuleBasedSourceRouter
from src.structured.analytical_request import AnalyticalRequest, RequestFilters, check_compatibility, validate_request
from src.structured.compiler import compile_request
from src.structured.models import ColumnRole, ColumnType, InvalidParameterError, StructuredIntent, UnsupportedRequestError
from src.structured.semantic_model import FACTS, METRICS, Dimension, Metric, Operation, SortOrder, TimeGrain, Unit
from src.structured.rendering import render_structured_answer
from src.structured.service import StructuredQueryService
from src.structured.text_match import SQL_ACCENTS_FROM, SQL_ACCENTS_TO, normalize_text, search_terms

M = Metric.STOCK_ON_HAND
ROUTER = RuleBasedSourceRouter()


class FakeExecutor:
    def __init__(self, rows=()):
        self.rows, self.calls = list(rows), []

    def execute(self, statement, parameters=None):
        self.calls.append((statement, dict(parameters or {})))
        return [dict(row) for row in self.rows]


def _request(question: str) -> AnalyticalRequest:
    decision = ROUTER.route(question)
    assert decision.structured_params, f"{question!r} was not turned into a request: {decision.unsupported_reason}"
    return decision.structured_params["request"]


def _refusal(question: str) -> str:
    decision = ROUTER.route(question)
    assert decision.route is SourceRoute.STRUCTURED_DATA and decision.unsupported_reason, question
    return decision.unsupported_reason


def _compile(request: AnalyticalRequest):
    return compile_request(request, catalog="test_catalog", schema="gold")


def _sql(question: str):
    return _compile(_request(question))


def _flat(sql: str) -> str:
    return re.sub(r"\s+", " ", sql)


def _answer(question: str, rows) -> str:
    executor = FakeExecutor(rows)
    harness = AIHarness(
        orchestrator=None,
        structured_query_service=StructuredQueryService(executor, catalog="test_catalog"),
        source_router=ROUTER,
    )
    result = harness.ask(question)
    assert result.status is HarnessStatus.STRUCTURED_DATA_OK, (question, result.status, result.error)
    return result.answer


# ---------------------------------------------------------------------------
# text matching: pure normalization, no entity knowledge
# ---------------------------------------------------------------------------


def test_the_sql_accent_map_folds_the_same_characters_as_the_python_normalization():
    assert len(SQL_ACCENTS_FROM) == len(SQL_ACCENTS_TO)
    for char, folded in zip(SQL_ACCENTS_FROM, SQL_ACCENTS_TO):
        assert normalize_text(char).replace(" ", "") == folded.strip(), char


@pytest.mark.parametrize(
    "phrase, terms",
    [
        ("coletes", (("colete",),)),
        ("Motor de Popa 6014", (("motor",), ("popa",), ("6014",))),  # "de" only links
        ("GPS Plotter", (("gps",), ("plotter",))),  # a short word is not stripped of its "s"
        ("Bússolas Náuticas", (("bussola",), ("nautica",))),  # accents folded, plural reduced
        ("Salva-Vidas", (("salva",), ("vida",))),  # punctuation splits words
        ("motores", (("motor",),)),
        ("botões", (("botoes", "botao"),)),
        ("canais", (("canais", "canal"),)),
    ],
)
def test_search_terms_are_letters_and_digits_with_simple_plural_variants(phrase, terms):
    assert search_terms(phrase) == terms


@pytest.mark.parametrize("hostile", ["x'; DROP TABLE t; --", "100%_", 'a" OR 1=1 --', "café\nnew line", "colete) UNION SELECT 1"])
def test_a_term_can_carry_neither_a_wildcard_nor_a_quote(hostile):
    for variants in search_terms(hostile):
        for term in variants:
            assert re.fullmatch(r"[a-z0-9]+", term), (hostile, term)


def test_a_phrase_with_too_many_words_is_refused_not_truncated():
    with pytest.raises(ValueError):
        search_terms("a1 b2 c3 d4 e5 f6 g7")


# ---------------------------------------------------------------------------
# 1. current inventory
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question, product",
    [
        ("Quantos coletes temos em estoque?", "coletes"),
        ("Quantos motores temos em estoque?", "motores"),
        ("Quanto temos do Motor de Popa 6014 em estoque?", "Motor de Popa 6014"),
        ("Quanto temos de Bateria Náutica 100Ah?", "Bateria Náutica 100Ah"),
        ("Quantos GPS Plotter temos?", "GPS Plotter"),
        ("Qual o estoque de bússolas?", "bússolas"),
        ("Quantas unidades de boias temos?", "boias"),
    ],
)
def test_a_product_named_in_the_question_is_a_filter_of_the_request(question, product):
    request = _request(question)

    assert (request.metric, request.operation, request.dimensions) == (M, Operation.AGGREGATE, ())
    assert request.filters.product_text == product  # never dropped
    assert request.as_of is None and request.filters.date_range is None


def test_current_stock_is_read_from_the_current_inventory_fact_and_never_filtered_by_date():
    query = _sql("Quantos coletes temos em estoque?")
    sql = _flat(query.statement)

    assert "FROM test_catalog.gold.fct_current_inventory AS i" in sql
    assert "INNER JOIN test_catalog.gold.dim_products AS p ON i.product_variant_id = p.product_variant_id" in sql
    assert "SUM(i.quantity_on_hand) AS stock_on_hand" in sql
    for forbidden in ("CURRENT_DATE", "current_date", "inventory_reference_date", "snapshot_valid", "fct_inventory_replenishment", ":as_of"):
        assert forbidden not in sql, forbidden
    assert query.source_tables == ("gold.fct_current_inventory", "gold.dim_products")


def test_stock_no_longer_borrows_quantity_on_hand_from_the_replenishment_fact():
    assert METRICS[M].fact == "inventory" and FACTS["inventory"].table == "fct_current_inventory"
    assert METRICS[M].historical_fact == "inventory_history" and FACTS["inventory_history"].table == "fct_inventory_snapshot"
    assert "fct_inventory_replenishment" not in _sql("Qual o estoque atual?").statement


def test_without_a_qualifier_the_total_is_unfiltered():
    query = _sql("Qual o estoque atual?")

    assert "WHERE" not in query.statement and "JOIN" not in query.statement and query.parameters == {}


@pytest.mark.parametrize("question", ["Quantos coletes temos em cada localidade?", "Quantos motores temos em cada estoque?", "Quanto temos de boias por loja?"])
def test_current_stock_by_location(question):
    request = _request(question)
    sql = _flat(_compile(request).statement)

    assert (request.operation, request.dimensions) == (Operation.BREAKDOWN, (Dimension.LOCATION,))
    assert request.filters.product_text and not request.positive_only
    assert "LEFT JOIN test_catalog.gold.dim_locations AS l ON i.location_id = l.location_id" in sql
    assert "GROUP BY l.location_name" in sql and "HAVING" not in sql


@pytest.mark.parametrize("question, product", [("Em quais estoques temos coletes?", "coletes"), ("Onde temos motores?", "motores"), ("Em quais lojas temos GPS Plotter em estoque?", "GPS Plotter")])
def test_positive_stock_location_listing_keeps_only_locations_with_stock(question, product):
    request = _request(question)
    sql = _flat(_compile(request).statement)

    assert (request.operation, request.dimensions, request.positive_only) == (Operation.BREAKDOWN, (Dimension.LOCATION,), True)
    assert request.filters.product_text == product
    assert "GROUP BY l.location_name HAVING SUM(i.quantity_on_hand) > 0" in sql


@pytest.mark.parametrize(
    "question, product, location",
    [
        ("Quantos coletes temos na Loja Leão?", "coletes", "Loja Leão"),
        ("Quanto estoque temos no Armazém Camargo?", None, "Armazém Camargo"),
        ("Quantos motores temos no Armazém de Santos?", "motores", "Armazém de Santos"),
        ("Quantas boias temos no centro de distribuição Norte?", "boias", "centro de distribuição Norte"),
        ("Quantos GPS Plotter temos na Loja Central?", "GPS Plotter", "Loja Central"),
    ],
)
def test_a_specific_location_is_a_typed_filter_and_never_disappears(question, product, location):
    request = _request(question)
    query = _compile(request)

    assert request.filters.location_text == location and request.filters.product_text == product
    assert request.dimensions == ()
    assert "l.location_name" in query.statement and "dim_locations" in query.statement
    location_terms = [v for k, v in query.parameters.items() if k.startswith("location_term_")]
    assert location_terms and all(re.fullmatch(r"%[a-z0-9]+%", v) for v in location_terms)
    assert {v.strip("%") for v in location_terms} >= {t[0] for t in search_terms(location)[:1]}


@pytest.mark.parametrize(
    "question, order, sql_order, tie_breakers, product_or_loc",
    [
        ("Quais produtos têm mais estoque?", None, "DESC", "product_id, product_name", None),
        ("Quais produtos têm menos estoque?", SortOrder.ASC, "ASC", "product_id, product_name", None),
        ("Quais produtos têm menos estoque na Loja Leão?", SortOrder.ASC, "ASC", "product_id, product_name", "Loja Leão"),
        ("Quais os 5 produtos com menos estoque no Armazém Camargo?", SortOrder.ASC, "ASC", "product_id, product_name", "Armazém Camargo"),
        ("Quais lojas têm mais estoque de coletes?", None, "DESC", "location_name", "coletes"),
    ],
)
def test_current_stock_ranking_top_and_bottom(question, order, sql_order, tie_breakers, product_or_loc):
    request = _request(question)
    sql = _flat(_compile(request).statement)

    assert request.operation is Operation.RANKING and request.ranking_order is order
    # bounded: exactly N items by the exact value, then the dimension's columns as tie-breakers
    assert f"ROW_NUMBER() OVER ( ORDER BY stock_on_hand {sql_order}, {tie_breakers} ) AS ranking" in sql
    assert "RANK()" not in sql
    assert "WHERE ranking <= :top_n" in sql
    if product_or_loc:
        assert product_or_loc in {request.filters.location_text, request.filters.product_text}


@pytest.mark.parametrize(
    "question, dimension",
    [("Qual o estoque por estado?", Dimension.LOCATION_STATE), ("Quantos coletes temos em cada estado?", Dimension.LOCATION_STATE), ("Estoque de motores por categoria", Dimension.CATEGORY)],
)
def test_stock_can_be_split_by_state_or_category(question, dimension):
    assert _request(question).dimensions == (dimension,)  # "estado" is unambiguous for stock: it only has location states


# ---------------------------------------------------------------------------
# 2. explicit category filter; a product phrase never searches the category
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question, category",
    [("Qual o estoque da categoria Segurança?", "Segurança"), ("Quais produtos da categoria Coletes Salva-Vidas temos em estoque?", "Coletes Salva-Vidas"), ("Quanto estoque temos na categoria Motores?", "Motores")],
)
def test_an_explicit_category_filters_category_name(question, category):
    request = _request(question)
    query = _compile(request)
    sql = _flat(query.statement)

    assert request.filters.category_text == category and request.filters.product_text is None
    assert "p.category_name" in sql and "p.product_name" not in sql.replace("p.product_id, p.product_name", "")
    assert any(key.startswith("category_term_") for key in query.parameters)


@pytest.mark.parametrize("question", ["Quantos coletes temos em estoque?", "Quanto temos do Motor de Popa 6014 em estoque?", "Quantos GPS Plotter temos?"])
def test_a_product_phrase_searches_product_name_only_never_category_name(question):
    sql = _sql(question).statement

    assert "p.product_name" in sql
    assert "category_name" not in sql
    assert "category_term" not in _sql(question).parameters


def test_product_and_category_can_be_combined_when_both_are_named():
    request = AnalyticalRequest(M, Operation.AGGREGATE, filters=RequestFilters(product_text="coletes", category_text="Segurança"))
    query = _compile(request)

    assert "p.product_name" in query.statement and "p.category_name" in query.statement
    assert query.source_tables == ("gold.fct_current_inventory", "gold.dim_products")  # one join serves both


# ---------------------------------------------------------------------------
# 3. historical inventory (SCD2, as of the end of a day)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question, as_of",
    [
        ("Quantos coletes havia nos estoques em janeiro de 2026?", date(2026, 1, 31)),  # a month -> its last day
        ("Quantos motores havia em fevereiro de 2026?", date(2026, 2, 28)),
        ("Quantos coletes havia em dezembro de 2025?", date(2025, 12, 31)),
        ("Quantos coletes havia em cada estoque em 15 de janeiro de 2026?", date(2026, 1, 15)),  # an exact day
        ("Qual era o estoque de motores em 2026-02-15?", date(2026, 2, 15)),
        ("Qual era o estoque de boias em 31/03/2026?", date(2026, 3, 31)),
    ],
)
def test_the_as_of_date_is_the_exact_day_or_the_end_of_the_month(question, as_of):
    request = _request(question)
    query = _compile(request)

    assert request.as_of == as_of and request.filters.date_range is None
    assert query.parameters["as_of"] == as_of.isoformat()
    assert query.applied_filters["period"] == f"as_of:{as_of.isoformat()}"


def test_historical_stock_uses_the_scd2_validity_interval_not_a_sum_of_snapshots():
    query = _sql("Quantos coletes havia nos estoques em janeiro de 2026?")
    sql = _flat(query.statement)

    assert "FROM test_catalog.gold.fct_inventory_snapshot AS i" in sql
    # valid over [snapshot_valid_from, snapshot_valid_to): the row in force at the END of the day
    assert "i.snapshot_valid_from < CAST(date_add(CAST(:as_of AS DATE), 1) AS TIMESTAMP)" in sql
    assert "(i.snapshot_valid_to IS NULL OR i.snapshot_valid_to >= CAST(date_add(CAST(:as_of AS DATE), 1) AS TIMESTAMP))" in sql
    assert "SUM(i.quantity_on_hand)" in sql
    for forbidden in ("snapshot_date", "is_current_snapshot", "BETWEEN", "CURRENT_DATE", "fct_current_inventory", "MAX("):
        assert forbidden not in sql, forbidden
    assert query.source_tables == ("gold.fct_inventory_snapshot", "gold.dim_products")


def test_the_validity_predicates_pick_exactly_one_row_per_position_at_the_boundaries():
    """The two predicates, evaluated on the boundary cases, in plain Python."""

    def valid(valid_from, valid_to, day):
        end = day + timedelta(days=1)  # D+1 00:00
        return valid_from < end and (valid_to is None or valid_to >= end)

    day = date(2026, 1, 31)
    midnight = date(2026, 2, 1)  # 2026-02-01 00:00
    # a row closed exactly at the next midnight was still in force at the end of the day; its successor was not
    assert valid(date(2026, 1, 1), midnight, day) and not valid(midnight, None, day)
    # a row replaced mid-day: the successor is the position at the end of the day
    assert not valid(date(2026, 1, 1), date(2026, 1, 31), day) and valid(date(2026, 1, 31), None, day)
    # a row that starts after the day is not part of it; an open row that started before is
    assert not valid(date(2026, 2, 3), None, day) and valid(date(2025, 6, 1), None, day)


def test_historical_product_and_location_combination():
    request = _request("Quantos coletes havia na Loja Leão em janeiro de 2026?")
    query = _compile(request)
    sql = _flat(query.statement)

    assert (request.filters.product_text, request.filters.location_text, request.as_of) == ("coletes", "Loja Leão", date(2026, 1, 31))
    assert "fct_inventory_snapshot" in sql and "p.product_name" in sql and "l.location_name" in sql
    assert set(query.source_tables) == {"gold.fct_inventory_snapshot", "gold.dim_products", "gold.dim_locations"}
    assert {"as_of", "product_term_0_0", "location_term_0_0", "location_term_1_0"} <= set(query.parameters)


def test_historical_positive_listing_by_location():
    request = _request("Em quais estoques havia coletes em janeiro de 2026?")
    sql = _flat(_compile(request).statement)

    assert (request.operation, request.dimensions, request.positive_only, request.as_of) == (
        Operation.BREAKDOWN, (Dimension.LOCATION,), True, date(2026, 1, 31)
    )
    assert "GROUP BY l.location_name HAVING SUM(i.quantity_on_hand) > 0" in sql and "fct_inventory_snapshot" in sql


def test_historical_ranking_is_supported_where_the_history_has_the_dimension():
    request = _request("Quais produtos tinham mais estoque em janeiro de 2026?")

    assert (request.operation, request.dimensions, request.as_of) == (Operation.RANKING, (Dimension.PRODUCT,), date(2026, 1, 31))


@pytest.mark.parametrize(
    "question",
    [
        "Quantos coletes havia?",  # past, no date
        "Quantos coletes tínhamos ontem em estoque?",  # relative
        "Quantos coletes havia em janeiro?",  # month without year
        "Quantos coletes temos em estoque em 2025?",  # a year is not a position
        "Quantos coletes havia entre janeiro e março de 2026?",  # a range is not a position
        "Qual foi a evolução mensal do estoque de coletes?",  # no inventory trend in this pass
        "Estoque de coletes por mês",
    ],
)
def test_a_historical_question_without_a_representable_date_is_refused(question):
    decision = ROUTER.route(question)

    assert decision.route is SourceRoute.STRUCTURED_DATA and decision.unsupported_reason and not decision.structured_params


def test_a_future_date_is_not_a_position():
    request = AnalyticalRequest(M, Operation.AGGREGATE, as_of=date.today() + timedelta(days=1))

    with pytest.raises(InvalidParameterError, match="future"):
        validate_request(request)


# ---------------------------------------------------------------------------
# 4. qualifier loss is fail-closed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        "Quantos coletes temos em estoque?",
        "Quantos motores temos em estoque?",
        "Quantos GPS Plotter temos?",
        "Quanto temos do Motor de Popa 6014 em estoque?",
        "Quantos coletes temos na Loja Leão?",
        "Quantos coletes havia em janeiro de 2026?",
        "Qual o estoque da categoria Segurança?",
    ],
)
def test_the_parser_never_returns_a_broader_request_than_the_question(question):
    request = _request(question)
    f = request.filters

    assert not (request.operation is Operation.AGGREGATE and request.dimensions == () and not (f.product_text or f.category_text or f.location_text or request.as_of))


@pytest.mark.parametrize(
    "question, reason",
    [
        ("Quantos coletes e bússolas temos em estoque?", "more than one"),  # two items
        ("Quanto estoque temos de coletes e de motores?", "more than one"),
        ("Quantos coletes temos em estoque em Santos?", "more than one"),  # a place without a type
        ("Quantos coletes temos nas lojas?", "type of location"),  # a type of place is not a name
        ("Qual o estoque do produto 10?", "identifier"),  # an id is not a product name
        ("Quantos coletes temos em estoque em 2025?", "one date or one month"),
        ("Quantos coletes havia?", "needs a date"),
        ("Quantos coletes estão em risco de ruptura?", "cannot be filtered by a named item"),  # metric without the filter
        ("Quantos motores precisam de reposição?", "cannot be filtered by a named item"),
        ("Qual o risco de ruptura na Loja Leão?", "filtering by a specific"),
        ("Qual a receita da Loja Leão?", "filtering by a specific"),
        ("Qual a receita no Armazém Camargo?", "filtering by a specific"),
        ("Quais produtos têm menos taxa de devolução?", "ascending"),  # 'menos' only where the metric allows it
        ("Quantos coletes temos, menos os da Loja Leão?", "ranking"),
    ],
)
def test_a_qualifier_that_cannot_be_represented_refuses_the_question(question, reason):
    assert reason in _refusal(question)


def test_a_refused_question_executes_nothing():
    executor = FakeExecutor([{"stock_on_hand": "1"}])
    harness = AIHarness(
        orchestrator=None,
        structured_query_service=StructuredQueryService(executor, catalog="test_catalog"),
        source_router=ROUTER,
    )

    for question in ("Quantos coletes temos em estoque em Santos?", "Quantos coletes temos nas lojas?", "Quantos coletes havia?"):
        assert harness.ask(question).status is HarnessStatus.STRUCTURED_DATA_UNSUPPORTED
    assert executor.calls == []


@pytest.mark.parametrize(
    "question",
    ["Como o estoque é calculado?", "Explique o estoque de segurança", "Qual o estoque de segurança?", "Quantos vendedores temos?", "Quantos clientes temos?"],
)
def test_documentary_and_other_families_are_not_captured_by_the_stock_grammar(question):
    params = ROUTER.route(question).structured_params or {}
    assert "request" not in params or params["request"].metric is not M


def test_unsupported_combinations_are_refused_by_the_compatibility_matrix_not_only_by_the_parser():
    with pytest.raises(UnsupportedRequestError, match="PRODUCT_TEXT"):  # sales metrics do not take the name filters yet
        check_compatibility(AnalyticalRequest(Metric.NET_REVENUE, Operation.AGGREGATE, filters=RequestFilters(product_text="coletes")))
    with pytest.raises(UnsupportedRequestError, match="LOCATION_TEXT"):
        check_compatibility(AnalyticalRequest(Metric.STOCKOUT_RISK, Operation.AGGREGATE, filters=RequestFilters(location_text="Loja Leão")))
    with pytest.raises(UnsupportedRequestError, match="no history"):
        check_compatibility(AnalyticalRequest(Metric.STOCKOUT_RISK, Operation.AGGREGATE, as_of=date(2026, 1, 31)))
    with pytest.raises(UnsupportedRequestError, match="opposite order"):
        check_compatibility(AnalyticalRequest(Metric.NET_REVENUE, Operation.RANKING, (Dimension.PRODUCT,), ranking_order=SortOrder.ASC))
    with pytest.raises(UnsupportedRequestError, match="single value"):
        check_compatibility(AnalyticalRequest(M, Operation.AGGREGATE, positive_only=True))
    with pytest.raises(UnsupportedRequestError, match="positive"):
        check_compatibility(AnalyticalRequest(Metric.NET_REVENUE, Operation.BREAKDOWN, (Dimension.CATEGORY,), positive_only=True))
    with pytest.raises(UnsupportedRequestError, match="does not support TREND"):  # there is no inventory trend in this pass
        check_compatibility(AnalyticalRequest(M, Operation.TREND, time_grain=TimeGrain.MONTH, as_of=date(2026, 1, 31)))
    with pytest.raises(UnsupportedRequestError):  # the health status only exists on the current position
        check_compatibility(AnalyticalRequest(M, Operation.BREAKDOWN, (Dimension.INVENTORY_HEALTH_STATUS,), as_of=date(2026, 1, 31)))


# ---------------------------------------------------------------------------
# 5. named parameters / injection safety
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hostile",
    ["coletes'; DROP TABLE gold.fct_sales; --", "x' OR '1'='1", "100%", "a_b", "colete) UNION SELECT * FROM secrets --", '"; SELECT 1'],
)
def test_user_text_never_reaches_the_statement(hostile):
    request = AnalyticalRequest(
        M, Operation.BREAKDOWN, (Dimension.LOCATION,),
        RequestFilters(product_text=hostile, category_text=hostile, location_text=hostile),
    )
    query = _compile(request)
    statement = query.statement

    for fragment in ("DROP", "UNION", "secrets", "OR '1'", "SELECT 1", hostile):
        assert fragment not in statement, fragment
    assert "'" not in statement.replace(f"'{SQL_ACCENTS_FROM}'", "").replace(f"'{SQL_ACCENTS_TO}'", "")  # only the two fixed fold literals
    for key, value in query.parameters.items():
        assert isinstance(value, str) and re.fullmatch(r"%[a-z0-9]+%", value), (key, value)
        assert f":{key}" in statement  # every parameter is bound by name


def test_a_name_filter_without_a_searchable_word_is_refused():
    for text in ("   ", "de do da", "%%%", "---"):
        with pytest.raises(InvalidParameterError):
            validate_request(AnalyticalRequest(M, Operation.AGGREGATE, filters=RequestFilters(product_text=text)))


def test_every_parameter_of_the_statement_is_declared_and_every_declared_one_is_used():
    for question in (
        "Quantos coletes havia na Loja Leão em janeiro de 2026?",
        "Quais produtos têm menos estoque na Loja Leão?",
        "Quantos motores temos na categoria Segurança?",
    ):
        query = _sql(question)
        used = set(re.findall(r":([a-z_0-9]+)", query.statement))
        assert used == set(query.parameters), question


# ---------------------------------------------------------------------------
# 6. rendering: the position, the as-of date, the filters, the source
# ---------------------------------------------------------------------------


def test_a_current_answer_states_the_position_without_claiming_it_was_updated_today():
    text = _answer("Quantos coletes temos em estoque?", [{"stock_on_hand": "1234"}])

    assert text == (
        'Na posição atual disponível no sistema, o estoque de produtos com "coletes" no nome é de 1.234 unidades.\n\n'
        "Fonte: camada Gold — fct_current_inventory + dim_products."
    )
    assert "hoje" not in text and "atualizado" not in text


def test_a_zero_stock_is_a_value_not_missing_data():
    text = _answer("Quantos motores temos em estoque?", [{"stock_on_hand": "0"}])

    assert 'de produtos com "motores" no nome é de 0 unidades.' in text


def test_current_stock_by_location_answer():
    text = _answer(
        "Quantos coletes temos em cada localidade?",
        [{"location_name": "Loja Leão", "stock_on_hand": "40"}, {"location_name": "Armazém Camargo", "stock_on_hand": "12"}, {"location_name": None, "stock_on_hand": "1"}],
    )

    assert text.startswith('Na posição atual disponível no sistema, o estoque por estabelecimento de produtos com "coletes" no nome é:')
    assert "- Loja Leão — 40 unidades\n- Armazém Camargo — 12 unidades\n- (não informado) — 1 unidade" in text
    assert text.endswith("Fonte: camada Gold — fct_current_inventory + dim_locations + dim_products.")


def test_the_positive_listing_says_it_only_lists_locations_with_stock():
    text = _answer("Em quais estoques temos motores?", [{"location_name": "Loja Leão", "stock_on_hand": "7"}])

    assert "(somente valores maiores que zero)" in text and "- Loja Leão — 7 unidades" in text


def test_a_named_location_and_category_are_stated_in_the_answer():
    at_place = _answer("Quantos motores temos no Armazém Camargo?", [{"stock_on_hand": "9"}])
    in_category = _answer("Qual o estoque da categoria Segurança?", [{"stock_on_hand": "9"}])

    assert 'de produtos com "motores" no nome no estabelecimento "Armazém Camargo" é de 9 unidades.' in at_place
    assert "o estoque na categoria Segurança é de 9 unidades." in in_category


@pytest.mark.parametrize(
    "question, order_word",
    [("Quais produtos têm menos estoque na Loja Leão?", "menor estoque"), ("Quais produtos têm mais estoque na Loja Leão?", "maior estoque")],
)
def test_the_ranking_title_follows_the_direction_asked(question, order_word):
    rows = [{"ranking": "1", "product_id": "1", "product_name": "Bússola Marítima", "stock_on_hand": "2"}]
    text = _answer(question, rows)

    assert text.startswith(f'Na posição atual disponível no sistema, o produto com {order_word} no estabelecimento "Loja Leão" foi:')
    assert "1. Bússola Marítima — 2 unidades" in text


def test_a_historical_answer_states_the_as_of_date_it_used():
    month = _answer("Quantos coletes havia nos estoques em janeiro de 2026?", [{"stock_on_hand": "1234"}])
    day = _answer("Quantos coletes havia em cada estoque em 15 de janeiro de 2026?", [{"location_name": "Loja Leão", "stock_on_hand": "3"}])
    listing = _answer("Em quais estoques havia coletes em janeiro de 2026?", [{"location_name": "Loja Leão", "stock_on_hand": "3"}])

    assert month == (
        'Na posição de estoque de 31/01/2026, o estoque de produtos com "coletes" no nome foi de 1.234 unidades.\n\n'
        "Fonte: camada Gold — fct_inventory_snapshot + dim_products."
    )
    assert day.startswith('Na posição de estoque de 15/01/2026, o estoque por estabelecimento de produtos com "coletes" no nome foi:')
    assert day.endswith("Fonte: camada Gold — fct_inventory_snapshot + dim_locations + dim_products.")
    assert listing.startswith("Na posição de estoque de 31/01/2026,") and "(somente valores maiores que zero) foi:" in listing
    for text in (month, day, listing):
        assert "posição atual" not in text and "test_catalog" not in text and "gold." not in text


def test_a_historical_product_and_location_answer_names_both_tables():
    text = _answer("Quantos coletes havia na Loja Leão em janeiro de 2026?", [{"stock_on_hand": "5"}])

    assert text.endswith("Fonte: camada Gold — fct_inventory_snapshot + dim_products + dim_locations.")
    assert 'no estabelecimento "Loja Leão" foi de 5 unidades.' in text


def test_a_one_day_period_of_another_metric_is_stated_as_that_day():
    text = _answer("Qual a receita em 15 de janeiro de 2026?", [{"net_revenue": "10"}])

    assert text.startswith("O faturamento líquido em 15/01/2026 foi de R$ 10,00.")


def test_structured_answers_still_expose_no_documentary_sources():
    executor = FakeExecutor([{"stock_on_hand": "3"}])
    harness = AIHarness(orchestrator=None, structured_query_service=StructuredQueryService(executor, catalog="c"), source_router=ROUTER)
    result = harness.ask("Quantos coletes temos em estoque?")

    assert result.rag_result is None and "[S" not in result.answer  # sources stay [] in the API for structured-only answers


# ---------------------------------------------------------------------------
# 7. what did not change
# ---------------------------------------------------------------------------


def test_replenishment_and_risk_metrics_stay_on_the_replenishment_fact():
    for metric in (Metric.STOCKOUT_RISK, Metric.DAYS_TO_STOCKOUT, Metric.REPLENISHMENT_REQUIRED):
        assert METRICS[metric].fact == "replenishment" and METRICS[metric].historical_fact is None
        request = AnalyticalRequest(metric, Operation.RANKING, (Dimension.PRODUCT,))
        assert "fct_inventory_replenishment" in _compile(request).statement

    text = _answer(
        "Quais produtos têm mais risco de ruptura?",
        [{"ranking": "1", "product_id": "1", "product_name": "Bússola", "stockout_risk_numerator": "1", "stockout_risk_denominator": "8", "stockout_risk": "0.125"}],
    )
    assert "posição atual" not in text and text.endswith("Fonte: camada Gold — fct_inventory_replenishment + dim_products.")


def test_the_health_status_breakdown_is_still_current_only():
    request = _request("Quantos itens temos por status de saúde do estoque?")

    assert request.metric is Metric.INVENTORY_ITEMS and request.dimensions == (Dimension.INVENTORY_HEALTH_STATUS,)
    assert "fct_current_inventory" in _compile(request).statement


@pytest.mark.parametrize(
    "question, metric, operation",
    [
        ("Qual o faturamento em 2025?", Metric.NET_REVENUE, Operation.AGGREGATE),
        ("Quais os 5 produtos mais vendidos em 2025?", Metric.TOTAL_QUANTITY_SOLD, Operation.RANKING),
        ("Qual a receita por categoria em 2025?", Metric.NET_REVENUE, Operation.BREAKDOWN),
        ("Qual o faturamento mensal em 2025?", Metric.NET_REVENUE, Operation.TREND),
        ("Qual o ticket médio?", Metric.AVERAGE_TICKET, Operation.AGGREGATE),
        ("Quantos clientes temos?", Metric.CUSTOMER_COUNT, Operation.AGGREGATE),
        ("Quantos pedidos temos?", Metric.EFFECTIVE_ORDERS, Operation.AGGREGATE),
    ],
)
def test_sales_and_customer_questions_are_parsed_as_before(question, metric, operation):
    request = _request(question)

    assert (request.metric, request.operation) == (metric, operation)
    assert request.as_of is None and not request.positive_only and request.ranking_order is None
    assert not (request.filters.product_text or request.filters.category_text or request.filters.location_text)


def test_a_sales_statement_is_unaffected_by_the_new_filter_machinery():
    query = _sql("Quais os 5 produtos mais vendidos em 2025?")

    assert "fct_sales" in query.statement and "LIKE" not in query.statement and "translate(" not in query.statement
    assert set(query.parameters) == {"date_from", "date_to", "top_n"}


# ---------------------------------------------------------------------------
# 8. stock is DECIMAL(14,3) in Gold: exact decimals, never an integer
# ---------------------------------------------------------------------------


def test_stock_is_a_decimal_quantity_while_units_sold_stays_an_integer():
    assert METRICS[M].unit is Unit.DECIMAL_UNITS and METRICS[Metric.TOTAL_QUANTITY_SOLD].unit is Unit.UNITS

    def metric_type(request):
        return next(c.type for c in _compile(request).columns if c.role is ColumnRole.METRIC)

    assert metric_type(AnalyticalRequest(M, Operation.AGGREGATE)) is ColumnType.DECIMAL
    assert metric_type(AnalyticalRequest(M, Operation.RANKING, (Dimension.PRODUCT,))) is ColumnType.DECIMAL
    assert metric_type(AnalyticalRequest(Metric.TOTAL_QUANTITY_SOLD, Operation.AGGREGATE)) is ColumnType.INT
    assert metric_type(AnalyticalRequest(Metric.TOTAL_QUANTITY_SOLD, Operation.RANKING, (Dimension.PRODUCT,))) is ColumnType.INT


def test_the_live_decimal_text_of_the_sql_api_is_parsed_exactly_not_rejected():
    executor = FakeExecutor([{"stock_on_hand": "555.970"}])  # exactly what the SQL API returns for DECIMAL(14,3)
    service = StructuredQueryService(executor, catalog="test_catalog")
    result = service.execute(
        StructuredIntent.ANALYTICS,
        {"request": AnalyticalRequest(M, Operation.AGGREGATE, filters=RequestFilters(product_text="coletes"))},
    )

    value = result.table.rows[0][0]
    assert value == Decimal("555.970") and isinstance(value, Decimal)


@pytest.mark.parametrize(
    "text, rendered",
    [
        ("555.970", "556 unidades"),
        ("128.940", "129 unidades"),
        ("112.780", "113 unidades"),
        ("73.250", "73 unidades"),
        ("2.760", "3 unidades"),
        ("2.000", "2 unidades"),
        ("1.000", "1 unidade"),
        ("0.500", "1 unidade"),  # rounded half up, and the noun follows the rounded number
        ("0.499", "0 unidades"),
        ("1.499", "1 unidade"),
        ("1.500", "2 unidades"),
        ("0.000", "0 unidades"),
        ("1234567.100", "1.234.567 unidades"),
        ("10", "10 unidades"),  # an integer-looking value from a fake or another backend
    ],
)
def test_decimal_stock_is_shown_as_whole_portuguese_units_rounded_half_up(text, rendered):
    answer = _answer("Quantos coletes temos em estoque?", [{"stock_on_hand": text}])

    assert f"é de {rendered}." in answer


def test_decimal_stock_in_a_breakdown_and_a_ranking_uses_the_same_format():
    breakdown = _answer(
        "Quantos coletes temos em cada localidade?",
        [{"location_name": "Loja Leão", "stock_on_hand": "555.970"}, {"location_name": "Armazém Camargo", "stock_on_hand": "1.000"}],
    )
    ranking = _answer(
        "Quais produtos têm mais estoque?",
        [
            {"ranking": "1", "product_id": "1", "product_name": "Bússola", "stock_on_hand": "2.000"},
            {"ranking": "2", "product_id": "2", "product_name": "Boia", "stock_on_hand": "1.250"},
        ],
    )

    assert "- Loja Leão — 556 unidades\n- Armazém Camargo — 1 unidade" in breakdown
    assert "1. Bússola — 2 unidades\n2. Boia — 1 unidade" in ranking


def test_units_sold_still_require_and_render_whole_numbers():
    rows = [{"quantity_sold": "1737"}]

    assert _answer("Quantas unidades vendemos em 2025?", rows).startswith("Foram vendidas 1.737 unidades em 2025.")
    assert _answer("Quantas unidades vendemos em 2025?", [{"quantity_sold": "1"}]).startswith("Foi vendida 1 unidade em 2025.")
    executor = FakeExecutor([{"quantity_sold": "555.970"}])  # a sales quantity is still validated as an integer
    harness = AIHarness(orchestrator=None, structured_query_service=StructuredQueryService(executor, catalog="c"), source_router=ROUTER)
    assert harness.ask("Quantas unidades vendemos em 2025?").status is not HarnessStatus.STRUCTURED_DATA_OK


# ---------------------------------------------------------------------------
# 9. stock rankings are bounded to N items; other rankings keep their ties
# ---------------------------------------------------------------------------


def _sqlite_rows(request: AnalyticalRequest, setup: list[str], *, params=None):
    """Run the compiled statement (three-part names reduced to table names, named
    parameters as they are) on an in-memory SQLite that has window functions, to
    check what a ranking really returns. Only SQL both engines share is used here."""
    import sqlite3

    query = _compile(request)
    connection = sqlite3.connect(":memory:")
    try:
        for statement in setup:
            connection.execute(statement)
        connection.row_factory = sqlite3.Row
        sql = query.statement.replace("test_catalog.gold.", "")
        return [dict(row) for row in connection.execute(sql, {**dict(query.parameters), **(params or {})})]
    finally:
        connection.close()


def _stock_setup(quantities: dict[int, float]) -> list[str]:
    """One product (one variant) per id, stocked in one location."""
    products = ", ".join(f"({i}, {i}, 'Produto {i:03d}', 'Cat')" for i in quantities)
    stock = ", ".join(f"({i}, 1, {q})" for i, q in quantities.items())
    return [
        "CREATE TABLE dim_products (product_variant_id INT, product_id INT, product_name TEXT, category_name TEXT)",
        "CREATE TABLE dim_locations (location_id INT, location_name TEXT, location_state TEXT)",
        "CREATE TABLE fct_current_inventory (product_variant_id INT, location_id INT, quantity_on_hand REAL)",
        f"INSERT INTO dim_products VALUES {products}",
        "INSERT INTO dim_locations VALUES (1, 'Loja Leão', 'SP')",
        f"INSERT INTO fct_current_inventory VALUES {stock}",
    ]


# hundreds of products share the same quantity, as in the live data
TIED_STOCK = {**{i: 5.0 for i in range(1, 401)}, 401: 0.4, 402: 0.6, 403: 1.4, 404: 9.0}


@pytest.mark.parametrize("question", ["Quais produtos têm menos estoque?", "Quais produtos têm mais estoque?"])
def test_a_stock_ranking_returns_at_most_top_n_products_despite_ties(question):
    request = _request(question)
    assert request.top_n is None  # the default of 10 applies

    rows = _sqlite_rows(request, _stock_setup(TIED_STOCK))

    assert len(rows) == 10
    assert [row["ranking"] for row in rows] == list(range(1, 11))  # 1..N, never a shared position
    assert len({row["product_id"] for row in rows}) == 10


def test_a_stock_ranking_with_an_explicit_top_n_is_bounded_too():
    request = _request("Quais os 5 produtos com menos estoque?")

    rows = _sqlite_rows(request, _stock_setup(TIED_STOCK))

    assert request.top_n == 5 and [row["ranking"] for row in rows] == [1, 2, 3, 4, 5]


def test_ties_are_broken_deterministically_by_product_id_and_the_exact_value_orders_first():
    ascending = _sqlite_rows(_request("Quais produtos têm menos estoque?"), _stock_setup(TIED_STOCK))
    descending = _sqlite_rows(_request("Quais produtos têm mais estoque?"), _stock_setup(TIED_STOCK))

    # exact values, not what is displayed: 0.4 < 0.6 < 1.4 although 0.6 and 1.4 both read "1 unidade"
    assert [r["product_id"] for r in ascending[:3]] == [401, 402, 403]
    assert [r["stock_on_hand"] for r in ascending[:4]] == [0.4, 0.6, 1.4, 5.0]
    assert ascending[3]["product_id"] == 1 and ascending[4]["product_id"] == 2  # the 5.0 ties, by product_id
    assert descending[0]["product_id"] == 404 and [r["product_id"] for r in descending[1:4]] == [1, 2, 3]
    # the same data returns the same rows every time
    assert ascending == _sqlite_rows(_request("Quais produtos têm menos estoque?"), _stock_setup(TIED_STOCK))


def test_a_stock_ranking_by_location_is_bounded_with_the_location_as_tie_breaker():
    request = AnalyticalRequest(M, Operation.RANKING, (Dimension.LOCATION,), top_n=2)
    sql = _flat(_compile(request).statement)

    assert "ROW_NUMBER() OVER ( ORDER BY stock_on_hand DESC, location_name ) AS ranking" in sql


def test_the_bounded_ranking_never_shows_the_tie_note_and_the_title_counts_the_rows():
    rows = [{"ranking": str(i), "product_id": str(i), "product_name": f"Produto {i}", "stock_on_hand": "5.000"} for i in range(1, 11)]
    text = _answer("Quais produtos têm menos estoque?", rows)

    assert text.startswith("Na posição atual disponível no sistema, os 10 produtos com menor estoque foram:")
    assert "empatados" not in text and "compartilham" not in text and "mais itens do que o pedido" not in text
    assert "1. Produto 1 — 5 unidades" in text and "10. Produto 10 — 5 unidades" in text


def test_a_ranking_that_preserves_ties_keeps_the_tie_note():
    rows = [
        {"ranking": "1", "product_id": "1", "product_name": "A", "net_revenue": "10"},
        {"ranking": "1", "product_id": "2", "product_name": "B", "net_revenue": "10"},
    ]
    text = _answer("Quais os 1 produtos com maior receita em 2025?", rows)

    assert "Produtos empatados compartilham a mesma posição" in text


def test_only_stock_gives_up_tie_preservation():
    assert [metric for metric, definition in METRICS.items() if not definition.preserve_ties] == [M]


@pytest.mark.parametrize(
    "request_",
    [
        AnalyticalRequest(Metric.STOCKOUT_RISK, Operation.RANKING, (Dimension.PRODUCT,)),
        AnalyticalRequest(Metric.REPLENISHMENT_REQUIRED, Operation.RANKING, (Dimension.PRODUCT,)),
        AnalyticalRequest(Metric.DAYS_TO_STOCKOUT, Operation.RANKING, (Dimension.PRODUCT,)),
        AnalyticalRequest(Metric.INVENTORY_ITEMS, Operation.RANKING, (Dimension.PRODUCT,)),
        AnalyticalRequest(Metric.NET_REVENUE, Operation.RANKING, (Dimension.PRODUCT,)),
        AnalyticalRequest(Metric.TOTAL_QUANTITY_SOLD, Operation.RANKING, (Dimension.PRODUCT,)),
        AnalyticalRequest(Metric.NET_REVENUE, Operation.RANKING, (Dimension.SALESPERSON,)),
    ],
    ids=lambda r: f"{r.metric.name}-{r.dimensions[0].name}",
)
def test_every_other_ranking_still_preserves_ties(request_):
    sql = _flat(_compile(request_).statement)
    expected = "RANK()" if request_.dimensions[0] is Dimension.SALESPERSON else "DENSE_RANK()"

    assert f" {expected} OVER (" in f" {sql}" and "ROW_NUMBER" not in sql


def test_a_sales_ranking_really_returns_every_tied_product():
    setup = [
        "CREATE TABLE dim_products (product_variant_id INT, product_id INT, product_name TEXT, category_name TEXT)",
        "CREATE TABLE fct_sales (product_variant_id INT, is_effective_sale INT, order_item_net_revenue_after_returns REAL)",
        "INSERT INTO dim_products VALUES " + ", ".join(f"({i}, {i}, 'P{i:03d}', 'C')" for i in range(1, 31)),
        "INSERT INTO fct_sales VALUES " + ", ".join(f"({i}, 1, {50 if i > 3 else 100 + i})" for i in range(1, 31)),
    ]
    request = AnalyticalRequest(Metric.NET_REVENUE, Operation.RANKING, (Dimension.PRODUCT,), top_n=5)

    rows = _sqlite_rows(request, setup)

    # 3 distinct top values (103, 102, 101) then 27 products tied at 50: dense rank 4 keeps all of them
    assert len(rows) == 30 and {row["ranking"] for row in rows} == {1, 2, 3, 4}


def test_the_stock_ranking_sql_never_rounds_or_casts_the_value():
    sql = _flat(_sql("Quais produtos têm menos estoque?").statement)

    assert "SUM(i.quantity_on_hand) AS stock_on_hand" in sql
    assert not re.search(r"\b(ROUND|FLOOR|CEIL|CEILING|TRUNC|TRUNCATE|CAST)\s*\(", sql.upper())


def test_the_service_keeps_the_exact_decimal_and_only_the_text_is_rounded():
    executor = FakeExecutor([{"stock_on_hand": "555.970"}])
    service = StructuredQueryService(executor, catalog="test_catalog")
    result = service.execute(StructuredIntent.ANALYTICS, {"request": AnalyticalRequest(M, Operation.AGGREGATE)})

    assert result.table.rows[0][0] == Decimal("555.970") and str(result.table.rows[0][0]) == "555.970"
    assert render_structured_answer(result).startswith("Na posição atual disponível no sistema, o estoque é de 556 unidades.")
    assert result.table.rows[0][0] == Decimal("555.970")  # rendering did not touch it


def test_sales_quantities_keep_whole_number_semantics_and_no_rounding():
    assert METRICS[Metric.TOTAL_QUANTITY_SOLD].unit is Unit.UNITS and METRICS[Metric.TOTAL_QUANTITY_SOLD].preserve_ties
    assert _answer("Quantas unidades vendemos em 2025?", [{"quantity_sold": "1"}]).startswith("Foi vendida 1 unidade em 2025.")
    executor = FakeExecutor([{"quantity_sold": "555.970"}])
    harness = AIHarness(orchestrator=None, structured_query_service=StructuredQueryService(executor, catalog="c"), source_router=ROUTER)
    assert harness.ask("Quantas unidades vendemos em 2025?").status is not HarnessStatus.STRUCTURED_DATA_OK  # still an integer
