"""Unit tests for src.harness.source_router — Phase 6B.1.

The router decides the *source family* (AI_SEARCH / STRUCTURED_DATA / BOTH).
It is deterministic, offline, and separate from the AI Search retrieval
policy (EXACT_RULE_ID / NORMAL / CROSS_SOURCE), which these tests also
confirm is not consulted or altered.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.harness.harness import SourceRoute
from src.harness.source_router import RuleBasedSourceRouter, normalize_query
from src.structured.models import StructuredIntent

ROUTER = RuleBasedSourceRouter()
TOP = StructuredIntent.TOP_PRODUCTS_BY_QUANTITY
DATASET_DIR = Path(__file__).resolve().parents[1] / "evaluation" / "dataset"


# ---------------------------------------------------------------------------
# The three canonical questions
# ---------------------------------------------------------------------------


def test_canonical_rule_question_routes_to_ai_search():
    decision = ROUTER.route("Como é calculado o ranking de produtos?")

    assert decision.route is SourceRoute.AI_SEARCH
    assert decision.structured_intent is None
    assert decision.unsupported_reason is None


def test_canonical_data_question_routes_to_structured_data():
    decision = ROUTER.route("Quais são os produtos mais vendidos?")

    assert decision.route is SourceRoute.STRUCTURED_DATA
    assert decision.structured_intent is TOP
    assert decision.unsupported_reason is None
    assert decision.structured_params == {}  # service default (10) applies


def test_canonical_combined_question_routes_to_both():
    decision = ROUTER.route("Quais são os produtos mais vendidos e qual regra define esse ranking?")

    assert decision.route is SourceRoute.BOTH
    assert decision.structured_intent is TOP
    assert decision.unsupported_reason is None


# ---------------------------------------------------------------------------
# Portuguese variants
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        "Quais são os produtos mais vendidos?",
        "quais sao os produtos mais vendidos",  # no accents / punctuation
        "QUAIS SÃO OS PRODUTOS MAIS VENDIDOS?!",
        "Produtos mais vendidos",
        "Me mostre os produtos mais vendidos",
        "Liste os produtos mais vendidos.",
        "Qual é o produto mais vendido?",
        "Quais os mais vendidos?",
        "Quais produtos que mais vendem?",
        "Top 5 produtos mais vendidos",
        "Ranking dos produtos por quantidade vendida",
    ],
)
def test_data_question_variants_route_to_structured_data(query):
    decision = ROUTER.route(query)

    assert decision.route is SourceRoute.STRUCTURED_DATA, query
    assert decision.structured_intent is TOP
    assert decision.unsupported_reason is None


@pytest.mark.parametrize(
    "query",
    [
        "Como é calculado o ranking de produtos?",
        "Como o ranking dos produtos mais vendidos é calculado?",
        "Qual a regra do ranking de produtos?",
        "O que significa ranking denso?",
        "Explique como é feito o ranking por quantidade",
        "Como funciona o ranking de produtos?",
    ],
)
def test_pure_rule_questions_stay_on_ai_search_even_when_they_mention_the_ranking(query):
    assert ROUTER.route(query).route is SourceRoute.AI_SEARCH, query


@pytest.mark.parametrize(
    "query",
    [
        "Quais são os produtos mais vendidos e qual regra define esse ranking?",
        "Quais são os produtos mais vendidos e como o ranking é calculado?",
        "Liste os produtos mais vendidos e explique o critério do ranking",
        "Mostre o top 5 produtos mais vendidos e qual é a regra de cálculo",
        "quais produtos mais vendidos? e o que significa esse ranking?",
    ],
)
def test_data_plus_rule_questions_route_to_both(query):
    decision = ROUTER.route(query)

    assert decision.route is SourceRoute.BOTH, query
    assert decision.structured_intent is TOP


# ---------------------------------------------------------------------------
# top_n extraction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query, expected",
    [
        ("Top 5 produtos mais vendidos", 5),
        ("top10 produtos mais vendidos", 10),
        ("Quais os 3 produtos mais vendidos?", 3),
        ("Quais são os cinco produtos mais vendidos?", 5),
        ("Liste os dez produtos mais vendidos", 10),
        ("Quais são os produtos mais vendidos?", None),
    ],
)
def test_top_n_is_extracted_when_stated(query, expected):
    assert ROUTER.route(query).structured_params.get("top_n") == expected


def test_out_of_range_top_n_is_passed_through_for_the_service_to_reject():
    # The router never silently fixes a bad value; the service validates it.
    assert ROUTER.route("Top 0 produtos mais vendidos").structured_params == {"top_n": 0}
    assert ROUTER.route("Top 500 produtos mais vendidos").structured_params == {"top_n": 500}


# ---------------------------------------------------------------------------
# Unsupported structured requests: controlled, never a silent AI_SEARCH fallback
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        "Quais são os produtos mais vendidos em 2024?",  # period
        "Quais são os produtos mais vendidos em março?",
        "Quais foram os produtos mais vendidos no último mês?",
        "Quais são os produtos mais vendidos por loja?",  # location
        "Quais são os produtos mais vendidos por estado?",
        "Quais são os produtos mais vendidos por receita?",  # different metric
        "Quais os produtos com maior faturamento?",
        "Quais são os produtos menos vendidos?",  # different ranking
        "Quais são os produtos mais vendidos em 2024 e qual regra define esse ranking?",  # BOTH, unsupported half
    ],
)
def test_unsupported_product_ranking_requests_are_flagged_not_answered_from_documents(query):
    decision = ROUTER.route(query)

    assert decision.route is SourceRoute.STRUCTURED_DATA, query
    assert decision.structured_intent is None  # nothing executable is proposed
    assert decision.unsupported_reason


@pytest.mark.parametrize(
    "query",
    [
        "Qual foi o faturamento do mês?",
        "Quantos pedidos foram feitos ontem?",
        "Quantas unidades vendemos?",
        "Qual a receita total?",
        "Qual o estoque atual do produto 10?",
        "Quais clientes mais compraram?",
        "Qual a previsão de demanda?",
    ],
)
def test_unimplemented_analytical_questions_are_flagged_unsupported(query):
    decision = ROUTER.route(query)

    assert decision.route is SourceRoute.STRUCTURED_DATA, query
    assert decision.structured_intent is None
    assert decision.unsupported_reason


def test_unsupported_requests_never_produce_sql_or_an_executable_intent():
    for query in ("Qual foi o faturamento do mês?", "Quais são os produtos mais vendidos por loja?"):
        decision = ROUTER.route(query)
        assert decision.structured_intent is None
        assert not decision.structured_params


# ---------------------------------------------------------------------------
# Documentary questions must be left alone
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        "GOLD-032",
        "GOLD-999",
        "O que é GOLD-032?",
        "Como a Receita Líquida é calculada?",  # mentions revenue but asks for the rule
        "Qual a regra do faturamento no Power BI?",
        "Como o Power BI calcula a medida Ticket Médio?",
        "Como o Ticket Médio é definido no Gold e implementado no Power BI?",
        "Qual é a definição de pedido de compra em aberto?",
        "Por que o valor total do pedido não pode ser somado na granularidade de item?",
        "uma pergunta qualquer",
        "Explique o estoque de segurança",
    ],
)
def test_documentary_questions_route_to_ai_search(query):
    decision = ROUTER.route(query)

    assert decision.route is SourceRoute.AI_SEARCH, query
    assert decision.structured_intent is None
    assert decision.unsupported_reason is None


@pytest.mark.parametrize("dataset", ["retrieval_queries_v1.jsonl", "retrieval_queries_v2.jsonl", "identifier_queries_v1.jsonl"])
def test_existing_evaluation_queries_all_stay_on_ai_search(dataset):
    """Regression guard: none of the queries the retrieval policy was
    validated on may be stolen by the new router."""
    queries = [json.loads(line)["query"] for line in (DATASET_DIR / dataset).read_text(encoding="utf-8").splitlines() if line.strip()]
    assert queries, dataset

    routed_away = [q for q in queries if ROUTER.route(q).route is not SourceRoute.AI_SEARCH]
    assert routed_away == []


# ---------------------------------------------------------------------------
# Phase 6B.1 fix pass — semantic variants, fail-closed qualifiers, precedence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        "Quais produtos venderam mais?",  # "venderam mais"
        "Qual produto vendeu mais?",  # "vendeu mais"
        "Quais produtos mais venderam?",  # "mais venderam"
        "Quais os produtos que mais venderam?",
        "Qual o produto campeão de vendas?",  # "campeão de vendas"
        "Quais itens mais saem?",  # "itens ... mais saem"
        "Quais itens mais vendem?",
        "produtos com mais unidades vendidas",  # "mais unidades vendidas"
        "produto mais popular",  # "mais popular"
        "Quais foram os produtos mais vendidos?",
        "Quais os melhores produtos em vendas?",
    ],
)
def test_supported_ranking_is_recognized_in_every_wording(query):
    decision = ROUTER.route(query)

    assert decision.route is SourceRoute.STRUCTURED_DATA, query
    assert decision.structured_intent is TOP
    assert decision.unsupported_reason is None


@pytest.mark.parametrize(
    "query",
    [
        "Como identificar os produtos mais vendidos no Power BI?",  # how-to wording
        "Qual a medida usada para produtos mais vendidos?",  # measure wording
        "Onde vejo os produtos mais vendidos no dashboard?",  # dashboard wording
        "Como o Power BI mostra os produtos mais vendidos?",
        "Como calculo os produtos mais vendidos?",
        "Explique o ranking dos produtos mais vendidos",
        "O que significa produto mais vendido?",
        "Onde encontro os itens mais vendidos?",
    ],
)
def test_documentation_and_how_to_questions_about_the_ranking_stay_on_ai_search(query):
    decision = ROUTER.route(query)

    assert decision.route is SourceRoute.AI_SEARCH, query
    assert decision.structured_intent is None and decision.unsupported_reason is None


@pytest.mark.parametrize(
    "query",
    [
        "Quais produtos mais venderam em 2024?",  # period
        "Quais produtos venderam mais em janeiro?",  # month
        "Quais produtos venderam mais unidades em 2023?",
        "Qual produto vende mais no verão?",
        "Quais produtos mais venderam na loja X?",  # store
        "Quais produtos vendem mais por categoria?",
        "Quais produtos geraram mais receita?",  # revenue metric
        "Quais produtos venderam menos?",  # bottom ranking
        "Quais lojas venderam mais?",  # stores ranking
        "Quais lojas mais venderam?",
        "Quais clientes compraram mais?",  # customers ranking
        "Quais clientes mais compraram?",
        "Qual foi o faturamento do mês?",
        "Qual o total de vendas por produto?",
        "Qual a margem do produto 10?",
    ],
)
def test_unsupported_analytical_requests_fail_closed_without_a_top_products_phrase(query):
    decision = ROUTER.route(query)

    assert decision.route is SourceRoute.STRUCTURED_DATA, query
    assert decision.structured_intent is None  # nothing executable is proposed
    assert not decision.structured_params
    assert decision.unsupported_reason


@pytest.mark.parametrize(
    "query",
    [
        "Quais são os produtos mais vendidos e qual a regra do ranking do período?",
        "Quais são os produtos mais vendidos e qual a regra de vendas efetivas por ano?",
        "Quais são os produtos mais vendidos e como o ranking é calculado no mês de fechamento?",
        "Liste os produtos mais vendidos e explique o critério do ranking",
        "Explique o ranking e liste os produtos mais vendidos",  # rule clause first
        "Quais produtos venderam mais e por que o ranking usa quantidade?",
    ],
)
def test_scope_words_inside_the_rule_clause_do_not_make_a_both_question_unsupported(query):
    decision = ROUTER.route(query)

    assert decision.route is SourceRoute.BOTH, query
    assert decision.structured_intent is TOP and decision.unsupported_reason is None


def test_scope_words_inside_the_data_clause_still_make_a_both_question_unsupported():
    decision = ROUTER.route("Quais produtos venderam mais em 2024 e qual a regra do ranking?")

    assert decision.route is SourceRoute.STRUCTURED_DATA
    assert decision.structured_intent is None and decision.unsupported_reason


def test_top_n_is_read_from_the_data_clause_only():
    decision = ROUTER.route("Quais são os 3 produtos mais vendidos e como o ranking dos 10 melhores é calculado?")

    assert decision.route is SourceRoute.BOTH
    assert decision.structured_params == {"top_n": 3}


@pytest.mark.parametrize(
    "query",
    [
        "Quais produtos venderam menos?",
        "Quais lojas venderam mais?",
        "Quais produtos geraram mais receita?",
        "Quais produtos mais venderam em 2024?",
    ],
)
def test_an_unsupported_question_never_reaches_the_structured_service_or_the_orchestrator(query):
    from unittest.mock import MagicMock

    from src.harness.harness import AIHarness, HarnessStatus

    service, orchestrator = MagicMock(), MagicMock()
    harness = AIHarness(orchestrator=orchestrator, structured_query_service=service, source_router=ROUTER)

    result = harness.ask(query)

    assert result.status is HarnessStatus.STRUCTURED_DATA_UNSUPPORTED
    service.execute.assert_not_called()  # no SQL is ever built or run
    orchestrator.generate.assert_not_called()  # and documentation never substitutes for the values


@pytest.mark.parametrize(
    "query",
    [
        "Quais são os produtos mais vendidos?",
        "Quais produtos venderam mais?",
        "Como o ranking é calculado?",
        "Quais produtos venderam menos?",
    ],
)
def test_routing_is_deterministic(query):
    assert ROUTER.route(query) == ROUTER.route(query)


# ---------------------------------------------------------------------------
# Layering
# ---------------------------------------------------------------------------


def test_router_is_independent_of_the_retrieval_policy_and_infrastructure():
    source = (Path(__file__).resolve().parents[1] / "src" / "harness" / "source_router.py").read_text(encoding="utf-8")

    for forbidden in ("retrieval.policy", "classify_query", "databricks", "SELECT ", "spark"):
        assert forbidden not in source, forbidden


def test_normalize_query_strips_accents_case_and_punctuation():
    assert normalize_query("  Quais SÃO os Produtos, MAIS vendidos?!  ") == "quais sao os produtos mais vendidos"
