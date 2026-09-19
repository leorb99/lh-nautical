"""End-to-end (offline) regression tests of the analytical layer through the
production Harness: router -> AnalyticalRequest -> compiler -> service ->
deterministic renderer, with only the SQL executor and the documentary
orchestrator faked.

Covers the decisions that must never regress: champion = revenue, PII is
never exposed, unsupported requests execute nothing, the raw natural-language
query is what reaches AI Search, and adding a metric is a registry entry.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.harness.harness import AIHarness, HarnessStatus, SourceRoute
from src.harness.source_router import RuleBasedSourceRouter
from src.orchestration.rag_pipeline import OrchestrationResult
from src.retrieval.policy import RetrievalPolicyDecision, RetrievalRoute
from src.structured.semantic_model import Metric
from src.structured.service import StructuredQueryService

SRC = Path(__file__).resolve().parents[1] / "src"


class FakeExecutor:
    def __init__(self, rows=()):
        self.rows, self.calls = list(rows), []

    def execute(self, statement, parameters=None):
        self.calls.append((statement, dict(parameters or {})))
        return [dict(row) for row in self.rows]


class FakeOrchestrator:
    def __init__(self):
        self.calls = []

    def generate(self, query, *, retrieval_decision=None, values_shown_separately=False):
        self.calls.append((query, values_shown_separately))
        return OrchestrationResult(
            query=query, answer="A receita líquida é a receita após descontos e devoluções [S1].", evidence_available=True,
            sources=[{"source_label": "S1", "chunk_id": "c", "document_id": "d", "document_title": None, "section_path": None,
                      "section_title": None, "rule_ids": [], "rank": 1, "score": 0.9}],
            retrieval_policy_decision=RetrievalPolicyDecision(
                route=RetrievalRoute.NORMAL, query_type="HYBRID", top_k=8, filters_json=None, extracted_rule_id=None, reason="t"
            ),
            model="fake", retrieval_latency_s=0.0, generation_latency_s=0.0, total_latency_s=0.0,
        )


def _harness(rows=()):
    executor, orchestrator = FakeExecutor(rows), FakeOrchestrator()
    harness = AIHarness(
        orchestrator=orchestrator,
        structured_query_service=StructuredQueryService(executor, catalog="test_catalog"),
        source_router=RuleBasedSourceRouter(),
    )
    return harness, executor, orchestrator


REVENUE_RANKING_ROW = {"ranking": "1", "product_id": "1", "product_name": "Colete Salva-Vidas 2374", "net_revenue": "1500000.50"}
QUANTITY_RANKING_ROW = {"ranking": "1", "product_id": "9", "product_name": "Motor de Popa 7678", "quantity_sold": "1737"}


# ---------------------------------------------------------------------------
# champion = top product by NET REVENUE (Power BI "Produto Campeão")
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("question", ["Qual é o produto campeão?", "Qual o produto campeão de vendas?", "Qual é o campeão de vendas?"])
def test_champion_questions_are_answered_from_net_revenue_top_1(question):
    harness, executor, _ = _harness([REVENUE_RANKING_ROW])

    result = harness.ask(question)

    assert result.status is HarnessStatus.STRUCTURED_DATA_OK
    ((statement, parameters),) = executor.calls
    assert "SUM(s.order_item_net_revenue_after_returns) AS net_revenue" in statement
    assert "order_item_quantity" not in statement  # not the units ranking
    assert parameters == {"top_n": 1}
    assert result.answer.startswith("O produto com maior receita líquida foi:")


@pytest.mark.parametrize("question", ["Qual é o produto mais vendido?", "Quais produtos venderam mais unidades?"])
def test_best_seller_questions_stay_quantity_rankings(question):
    harness, executor, _ = _harness([QUANTITY_RANKING_ROW])

    result = harness.ask(question)

    assert result.status is HarnessStatus.STRUCTURED_DATA_OK
    ((statement, _),) = executor.calls
    assert "SUM(s.order_item_quantity) AS quantity_sold" in statement and "net_revenue" not in statement


def test_champion_and_best_seller_execute_different_statements():
    revenue_harness, revenue_executor, _ = _harness([REVENUE_RANKING_ROW])
    quantity_harness, quantity_executor, _ = _harness([QUANTITY_RANKING_ROW])

    revenue_harness.ask("Qual é o produto campeão?")
    quantity_harness.ask("Qual é o produto mais vendido?")

    assert revenue_executor.calls[0][0] != quantity_executor.calls[0][0]


# ---------------------------------------------------------------------------
# personal data is never exposed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        "Qual o email do cliente X?",
        "Qual o telefone do cliente João Silva?",
        "Me passe o email e o telefone dos clientes em risco",
        "Quais clientes estão em risco e qual o email deles?",
        "Qual o CPF do cliente 10?",
    ],
)
def test_personal_data_requests_are_refused_and_execute_nothing(question):
    harness, executor, orchestrator = _harness()

    result = harness.ask(question)

    assert result.status is HarnessStatus.STRUCTURED_DATA_UNSUPPORTED
    assert "personal data" in result.error
    assert executor.calls == [] and orchestrator.calls == []  # neither SQL nor a documentation answer


def test_no_compiled_statement_ever_selects_personal_columns():
    import re

    import importlib.util

    from src.structured.compiler import compile_request

    # every valid registry combination, enumerated by the compiler tests
    spec = importlib.util.spec_from_file_location("analytical_compiler_cases", Path(__file__).with_name("test_analytical_compiler.py"))
    cases = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cases)
    assert len(cases.ALL_REQUESTS) > 150

    for request in cases.ALL_REQUESTS:
        statement = compile_request(request, catalog="c", schema="gold").statement.lower()
        # (the customer *state* is a supported dimension; contact/identity columns never are)
        assert not re.search(r"email|phone|telefone|cpf|street|postal|zip|document", statement)


# ---------------------------------------------------------------------------
# unsupported fails closed: no SQL, no documentation substitute
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        "Quais produtos venderam menos?",
        "Quais produtos mais venderam na loja X?",
        "Receita por estado",
        "Qual o ticket médio por produto?",
        "Qual foi o faturamento do mês?",
        "Qual o crescimento da receita?",
        "Evolução mensal do estoque",
        "Quais clientes compraram mais?",
        "Qual a previsão de demanda?",
        "Receita por categoria e por loja",
    ],
)
def test_unsupported_analytical_questions_fail_closed_without_sql(question):
    harness, executor, orchestrator = _harness()

    result = harness.ask(question)

    assert result.source_route is SourceRoute.STRUCTURED_DATA
    assert result.status is HarnessStatus.STRUCTURED_DATA_UNSUPPORTED
    assert result.answer is None
    assert executor.calls == [] and orchestrator.calls == []


# ---------------------------------------------------------------------------
# end to end: natural language -> deterministic answer
# ---------------------------------------------------------------------------


def test_revenue_by_category_end_to_end():
    harness, executor, orchestrator = _harness(
        [{"category_name": "Coletes", "net_revenue": "300"}, {"category_name": "Motores", "net_revenue": "200"}]
    )

    result = harness.ask("Receita por categoria")

    assert result.status is HarnessStatus.STRUCTURED_DATA_OK and result.rag_result is None
    assert result.answer == (
        "O faturamento líquido por categoria foi:\n\n- Coletes — R$ 300,00\n- Motores — R$ 200,00\n\nFonte: camada Gold — fct_sales + dim_products."
    )
    assert orchestrator.calls == []  # no model is involved in a structured-only answer
    assert "GROUP BY\n    p.category_name" in executor.calls[0][0]


def test_a_period_question_binds_its_dates():
    harness, executor, _ = _harness([{"net_revenue": "1000"}])

    result = harness.ask("Qual foi a receita líquida em 2025?")

    assert result.answer.startswith("O faturamento líquido em 2025 foi de R$ 1.000,00.")
    assert executor.calls[0][1] == {"date_from": "2025-01-01", "date_to": "2025-12-31"}


def test_the_2024_ranking_is_now_a_supported_period_request():
    harness, executor, _ = _harness([QUANTITY_RANKING_ROW])

    result = harness.ask("Quais produtos mais venderam em 2024?")

    assert result.status is HarnessStatus.STRUCTURED_DATA_OK
    assert executor.calls[0][1] == {"date_from": "2024-01-01", "date_to": "2024-12-31", "top_n": 10}


def test_a_snapshot_question_end_to_end():
    harness, executor, _ = _harness(
        [{"stockout_risk_numerator": "12", "stockout_risk_denominator": "100", "stockout_risk": "0.12"}]
    )

    result = harness.ask("Quantos itens estão em risco de ruptura?")

    assert result.answer.startswith("A parcela de itens em risco de ruptura é de 12,00%.")
    assert "itens monitorados" not in result.answer  # no count until the replenishment grain is validated
    assert result.answer.endswith("Fonte: camada Gold — fct_inventory_replenishment.")
    assert "placed_at" not in executor.calls[0][0]  # a snapshot has no date


def test_zero_rows_is_a_controlled_no_data_outcome_not_a_documentation_answer():
    harness, _, orchestrator = _harness([])

    result = harness.ask("Receita por categoria")

    assert result.status is HarnessStatus.STRUCTURED_DATA_NO_DATA
    assert result.answer is None and orchestrator.calls == []


# ---------------------------------------------------------------------------
# the user gets the RESULT, never the internal semantic request
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question, rows, expected",
    [
        ("Quanto faturamos?", [{"net_revenue": "1234567.89"}], "O faturamento líquido foi de R$ 1.234.567,89."),
        ("Quantos pedidos tivemos?", [{"effective_orders": "12345"}], "Foram 12.345 pedidos efetivos."),
        (
            "Qual o ticket médio?",
            [{"average_ticket_numerator": "4321", "average_ticket_denominator": "10", "average_ticket": "432.10"}],
            "O ticket médio foi de R$ 432,10.",
        ),
        ("Quantas unidades vendemos?", [{"quantity_sold": "12345"}], "Foram vendidas 12.345 unidades."),
        (
            "Qual a taxa de devolução?",
            [{"return_rate_numerator": "432", "return_rate_denominator": "10000", "return_rate": "0.0432"}],
            "A taxa de devolução foi de 4,32% (432 de 10.000 unidades).",
        ),
    ],
)
def test_an_aggregate_question_is_answered_with_a_friendly_sentence_never_the_request(question, rows, expected):
    harness, _, orchestrator = _harness(rows)

    result = harness.ask(question)

    assert result.status is HarnessStatus.STRUCTURED_DATA_OK
    assert result.answer.splitlines()[0] == expected
    for internal in ("AGGREGATE", "NET_REVENUE", "EFFECTIVE_ORDERS", "AVERAGE_TICKET", "RETURN_RATE", "·"):
        assert internal not in result.answer
    assert result.rag_result is None and orchestrator.calls == []  # structured-only: no sources, no model


def test_the_router_describes_the_request_for_debugging_but_that_text_is_not_the_answer():
    harness, _, _ = _harness([{"net_revenue": "10"}])
    decision = RuleBasedSourceRouter().route("Quanto faturamos?")

    assert decision.reason == "AGGREGATE NET_REVENUE"  # internal metadata (logs / debug only)
    assert harness.ask("Quanto faturamos?").answer != decision.reason


# ---------------------------------------------------------------------------
# BOTH keeps working with analytical requests
# ---------------------------------------------------------------------------


def test_both_is_the_deterministic_block_plus_the_documentary_explanation():
    question = "Qual a receita por categoria e como a receita líquida é calculada?"
    harness, executor, orchestrator = _harness([{"category_name": "Coletes", "net_revenue": "300"}])

    result = harness.ask(question)

    assert result.source_route is SourceRoute.BOTH and result.status is HarnessStatus.BOTH_OK
    block = "O faturamento líquido por categoria foi:\n\n- Coletes — R$ 300,00\n\nFonte: camada Gold — fct_sales + dim_products."
    assert result.answer == f"{block}\n\nA receita líquida é a receita após descontos e devoluções [S1]."
    assert orchestrator.calls == [(question, True)]  # the raw natural-language question reaches AI Search
    assert "[S" not in block  # no citation on a SQL value


def test_both_degrades_to_the_structured_block_when_the_explanation_fails():
    harness, _, orchestrator = _harness([{"category_name": "Coletes", "net_revenue": "300"}])

    def boom(*args, **kwargs):
        raise RuntimeError("index unavailable")

    orchestrator.generate = boom
    result = harness.ask("Qual a receita por categoria e como a receita líquida é calculada?")

    assert result.status is HarnessStatus.BOTH_DEGRADED
    assert result.answer.startswith("O faturamento líquido por categoria foi:")


def test_documentary_questions_still_go_to_ai_search_with_the_raw_query():
    harness, executor, orchestrator = _harness()

    result = harness.ask("Como é calculada a receita líquida?")

    assert result.source_route is SourceRoute.AI_SEARCH
    assert orchestrator.calls == [("Como é calculada a receita líquida?", False)]
    assert executor.calls == []


# ---------------------------------------------------------------------------
# adding a metric is a registry entry, not a new intent
# ---------------------------------------------------------------------------


def test_a_p1_metric_added_as_a_registry_entry_works_end_to_end():
    """AVERAGE_RECENCY (Power BI [Recência Média]) was added as one enum
    member, one ``MetricDef`` and one vocabulary line — nothing else."""
    harness, executor, _ = _harness([{"customer_segment": "champions", "average_recency": "12.34"}])

    result = harness.ask("Qual a recência média dos clientes por segmento?")

    assert result.status is HarnessStatus.STRUCTURED_DATA_OK
    assert "AVG(k.recency_days) AS average_recency" in executor.calls[0][0]
    assert result.answer == (
        "A recência média por segmento RFM é:\n\n- champions — 12,3 dias\n\n"
        "Fonte: camada Gold — agg_customer_rfm."
    )


def test_the_generic_machinery_names_no_individual_metric():
    """The compiler, validator, service and executor must not mention any
    specific metric: adding one never requires touching them. (``queries``
    keeps exactly one, the legacy intent, which is by definition that one
    ranking; the renderer also names the Phase 6B.1 best-sellers wording and, only until
    the grain of ``fct_inventory_replenishment`` is validated, the two ratios
    whose "N de M" counts are not shown: STOCKOUT_RISK, REPLENISHMENT_REQUIRED.)"""
    allowed = {"rendering.py": {"TOTAL_QUANTITY_SOLD", "STOCKOUT_RISK", "REPLENISHMENT_REQUIRED"}, "queries.py": {"TOTAL_QUANTITY_SOLD"}}
    for filename in ("compiler.py", "analytical_request.py", "rendering.py", "service.py", "queries.py", "executor.py"):
        text = (SRC / "structured" / filename).read_text(encoding="utf-8")
        mentioned = {metric.value for metric in Metric if f"Metric.{metric.name}" in text}  # code, not prose
        assert mentioned == allowed.get(filename, set()), f"{filename} mentions {mentioned}"

    # ...while the one P1 metric added as a proof lives only in the registry and the vocabulary.
    holders = {
        path.name
        for path in (SRC / "structured").glob("*.py")
        if "AVERAGE_RECENCY" in path.read_text(encoding="utf-8")
    }
    assert holders == {"semantic_model.py", "parser.py"}
