"""Unit tests for src.harness.source_router.

The router decides the *source family* (AI_SEARCH / STRUCTURED_DATA / BOTH).
It is deterministic and offline. For a data question it delegates the
language -> ``AnalyticalRequest`` mapping to ``src.structured.parser`` (whose
own tests are in ``test_analytical_parser.py``); these tests pin the routing
behavior: which family, which request, and that unsupported analytical
questions fail closed instead of falling back to documentation.

The AI Search retrieval policy (EXACT_RULE_ID / NORMAL / CROSS_SOURCE) is
never consulted or altered, which these tests also confirm.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.harness.harness import SourceRoute
from src.harness.source_router import RuleBasedSourceRouter, normalize_query
from src.structured.analytical_request import AnalyticalRequest
from src.structured.models import StructuredIntent
from src.structured.semantic_model import Dimension, Metric, Operation

ROUTER = RuleBasedSourceRouter()
DATASET_DIR = Path(__file__).resolve().parents[1] / "evaluation" / "dataset"


def _request(decision) -> AnalyticalRequest:
    assert decision.structured_intent is StructuredIntent.ANALYTICS
    return decision.structured_params["request"]


def _quantity_ranking(top_n=None) -> AnalyticalRequest:
    return AnalyticalRequest(Metric.TOTAL_QUANTITY_SOLD, Operation.RANKING, (Dimension.PRODUCT,), top_n=top_n)


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
    assert _request(decision) == _quantity_ranking()  # service default (10) applies
    assert decision.unsupported_reason is None


def test_canonical_combined_question_routes_to_both():
    decision = ROUTER.route("Quais são os produtos mais vendidos e qual regra define esse ranking?")

    assert decision.route is SourceRoute.BOTH
    assert _request(decision) == _quantity_ranking()
    assert decision.unsupported_reason is None


# ---------------------------------------------------------------------------
# The Phase 6B.1 top-products wordings still work (now as analytical requests)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query, top_n",
    [
        ("Quais são os produtos mais vendidos?", None),
        ("quais sao os produtos mais vendidos", None),  # no accents / punctuation
        ("QUAIS SÃO OS PRODUTOS MAIS VENDIDOS?!", None),
        ("Produtos mais vendidos", None),
        ("Me mostre os produtos mais vendidos", None),
        ("Liste os produtos mais vendidos.", None),
        ("Quais os mais vendidos?", None),
        ("Quais produtos que mais vendem?", None),
        ("Ranking dos produtos por quantidade vendida", None),
        ("Qual é o produto mais vendido?", 1),  # singular subject
        ("Top 5 produtos mais vendidos", 5),
        ("top10 produtos mais vendidos", 10),
        ("Quais os 3 produtos mais vendidos?", 3),
        ("Quais são os cinco produtos mais vendidos?", 5),
        ("Liste os dez produtos mais vendidos", 10),
    ],
)
def test_top_products_wordings_route_to_the_quantity_ranking(query, top_n):
    decision = ROUTER.route(query)

    assert decision.route is SourceRoute.STRUCTURED_DATA, query
    assert _request(decision) == _quantity_ranking(top_n), query
    assert decision.unsupported_reason is None


def test_out_of_range_top_n_is_passed_through_for_the_service_to_reject():
    # The router never silently fixes a bad value; the service validates it.
    assert _request(ROUTER.route("Top 0 produtos mais vendidos")).top_n == 0
    assert _request(ROUTER.route("Top 500 produtos mais vendidos")).top_n == 500


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
        "Explique o ranking e liste os produtos mais vendidos",  # rule clause first
    ],
)
def test_data_plus_rule_questions_route_to_both(query):
    decision = ROUTER.route(query)

    assert decision.route is SourceRoute.BOTH, query
    assert _request(decision).metric is Metric.TOTAL_QUANTITY_SOLD


# ---------------------------------------------------------------------------
# Supported wordings of the same ranking; documentation wording stays documentary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        "Quais produtos venderam mais?",
        "Qual produto vendeu mais?",
        "Quais produtos mais venderam?",
        "Quais os produtos que mais venderam?",
        "Quais itens mais saem?",
        "Quais itens mais vendem?",
        "produtos com mais unidades vendidas",
        "Quais produtos venderam mais unidades?",
        "produto mais popular",
        "Quais foram os produtos mais vendidos?",
        "Quais os melhores produtos em vendas?",
    ],
)
def test_supported_ranking_is_recognized_in_every_wording(query):
    decision = ROUTER.route(query)

    assert decision.route is SourceRoute.STRUCTURED_DATA, query
    request = _request(decision)
    assert (request.operation, request.metric, request.dimensions) == (
        Operation.RANKING,
        Metric.TOTAL_QUANTITY_SOLD,
        (Dimension.PRODUCT,),
    )
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
        "Como funciona o ranking de produtos no Power BI?",
    ],
)
def test_documentation_and_how_to_questions_about_the_ranking_stay_on_ai_search(query):
    decision = ROUTER.route(query)

    assert decision.route is SourceRoute.AI_SEARCH, query
    assert decision.structured_intent is None and decision.unsupported_reason is None


def test_a_paraphrase_the_vocabulary_does_not_know_falls_back_to_documentation_safely():
    # Known, safe false negative (documented): no SQL, no invented values.
    decision = ROUTER.route("Quais produtos são mais populares?")

    assert decision.route is SourceRoute.AI_SEARCH
    assert decision.structured_intent is None


# ---------------------------------------------------------------------------
# Unsupported analytical requests: controlled, never a silent AI_SEARCH fallback
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        "Quais são os produtos mais vendidos em março?",  # month without a year
        "Quais foram os produtos mais vendidos no último mês?",  # relative period
        "Quais produtos venderam mais em janeiro?",
        "Quais produtos mais venderam na loja X?",  # a specific store
        "Quais são os produtos mais vendidos por loja?",  # ranking by two dimensions
        "Quais são os produtos mais vendidos por estado?",  # ambiguous state
        "Quais são os produtos menos vendidos?",  # ascending ranking
        "Quais produtos venderam menos?",
        "Quais são os produtos mais vendidos em março e qual regra define esse ranking?",  # BOTH, unsupported half
        "Quais clientes compraram mais?",  # customer ranking
        "Quais clientes mais compraram?",
        "Qual foi o faturamento do mês?",  # relative period
        "Quantos pedidos foram feitos ontem?",
        "Qual o estoque atual do produto 10?",  # a specific product
        "Qual a previsão de demanda?",  # forecast: not part of the semantic model
        "Qual o total de vendas por produto?",  # 'vendas': revenue or units?
        "Qual o email do cliente X?",  # personal data
    ],
)
def test_unsupported_analytical_requests_fail_closed(query):
    decision = ROUTER.route(query)

    assert decision.route is SourceRoute.STRUCTURED_DATA, query
    assert decision.structured_intent is None  # nothing executable is proposed
    assert not decision.structured_params
    assert decision.unsupported_reason


def test_period_words_in_the_rule_clause_do_not_make_a_both_question_unsupported():
    for query in (
        "Quais são os produtos mais vendidos e qual a regra do ranking do período?",
        "Quais são os produtos mais vendidos e qual a regra de vendas efetivas por ano?",
        "Quais são os produtos mais vendidos e como o ranking é calculado no mês de fechamento?",
        "Quais produtos venderam mais e por que o ranking usa quantidade?",
    ):
        decision = ROUTER.route(query)
        assert decision.route is SourceRoute.BOTH, query
        assert decision.unsupported_reason is None


def test_period_words_in_the_data_clause_still_make_a_both_question_unsupported():
    decision = ROUTER.route("Quais produtos venderam mais em março e qual a regra do ranking?")

    assert decision.route is SourceRoute.STRUCTURED_DATA
    assert decision.unsupported_reason and decision.structured_intent is None


def test_top_n_is_read_from_the_data_clause_only():
    decision = ROUTER.route("Quais são os 3 produtos mais vendidos e como o ranking dos 10 melhores é calculado?")

    assert decision.route is SourceRoute.BOTH
    assert _request(decision).top_n == 3


def test_routing_is_deterministic():
    for query in (
        "Quais são os produtos mais vendidos?",
        "Receita por categoria",
        "Como o ranking é calculado?",
        "Quais produtos venderam menos?",
    ):
        assert ROUTER.route(query) == ROUTER.route(query)


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
        "Quando uma devolução é considerada efetiva para fins de cálculo de receita?",
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
    validated on may be stolen by the router."""
    queries = [json.loads(line)["query"] for line in (DATASET_DIR / dataset).read_text(encoding="utf-8").splitlines() if line.strip()]
    assert queries, dataset

    routed_away = [q for q in queries if ROUTER.route(q).route is not SourceRoute.AI_SEARCH]
    assert routed_away == []


# ---------------------------------------------------------------------------
# Layering
# ---------------------------------------------------------------------------


def test_router_is_independent_of_the_retrieval_policy_and_infrastructure():
    source = (Path(__file__).resolve().parents[1] / "src" / "harness" / "source_router.py").read_text(encoding="utf-8")

    for forbidden in ("retrieval.policy", "classify_query", "databricks", "SELECT ", "spark"):
        assert forbidden not in source, forbidden


def test_normalize_query_strips_accents_case_and_punctuation():
    assert normalize_query("  Quais SÃO os Produtos, MAIS vendidos?!  ") == "quais sao os produtos mais vendidos"
