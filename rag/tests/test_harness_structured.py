"""Unit tests for the Phase 6B.1 Harness behavior: source routing plus the
STRUCTURED_DATA and BOTH routes. Only in-memory fakes (and a real
RagOrchestrator with just the AI Search call and the LLM faked) — no
Databricks, no SQL, no MLflow. Existing Harness behavior is covered,
unchanged, by test_harness.py.

Structured values are rendered deterministically (see
test_structured_rendering.py); the tests here assert on the FINAL answer the
Harness returns and prove that no model is called to produce it.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from llama_index.core.base.llms.types import CompletionResponse, LLMMetadata
from llama_index.core.llms import CustomLLM

from src.evaluation.citations import CitationIntegrityStatus, check_citation_integrity
from src.harness.harness import (
    AIHarness,
    HarnessResult,
    HarnessStatus,
    RouteDecision,
    SourceRoute,
)
from src.harness.source_router import RuleBasedSourceRouter
from src.orchestration.rag_pipeline import OrchestrationResult, RagOrchestrator
from src.retrieval.ai_search import RetrievedChunk
from src.retrieval.policy import RetrievalPolicyDecision, RetrievalRoute
from src.structured.analytical_request import AnalyticalRequest
from src.structured.models import (
    InvalidParameterError,
    StructuredExecutionError,
    StructuredIntent,
    StructuredResult,
    TopProductRow,
)
from src.structured.rendering import render_structured_answer
from src.structured.semantic_model import Dimension, Metric, Operation
from src.structured.service import StructuredQueryService

TOP = StructuredIntent.TOP_PRODUCTS_BY_QUANTITY
ANALYTICS = StructuredIntent.ANALYTICS


def _quantity_ranking(top_n=None) -> AnalyticalRequest:
    """What the router derives from "produtos mais vendidos": the Phase 6B.1
    top-products ranking, expressed as an analytical request."""
    return AnalyticalRequest(Metric.TOTAL_QUANTITY_SOLD, Operation.RANKING, (Dimension.PRODUCT,), top_n=top_n)
DATA_Q = "Quais são os produtos mais vendidos?"
DOC_Q = "Como é calculado o ranking de produtos?"
BOTH_Q = "Quais são os produtos mais vendidos e qual regra define esse ranking?"

ROWS = (
    TopProductRow(1, 456, "Colete Salva-Vidas 2374", 1737),
    TopProductRow(2, 77, "Bateria Náutica 1945", 1731),
    TopProductRow(8, 55, "GPS Plotter 6249", 1638),
    TopProductRow(8, 81, "Motor de Popa 7678", 1638),
)


def _structured_result(rows=ROWS) -> StructuredResult:
    return StructuredResult(
        intent=TOP,
        rows=tuple(rows),
        row_count=len(rows),
        source_tables=("gold.fct_sales", "gold.dim_products"),
        catalog="test_catalog",
        applied_filters={"population": "effective_sales", "period": "all", "location": "all", "top_n": 10},
    )


def _rag_result(answer="regra do ranking [S1]", evidence=True) -> OrchestrationResult:
    return OrchestrationResult(
        query="q",
        answer=answer,
        evidence_available=evidence,
        sources=[{"source_label": "S1", "chunk_id": "c1", "document_id": "d", "document_title": None,
                  "section_path": None, "section_title": None, "rule_ids": [], "rank": 1, "score": 0.9}] if evidence else [],
        retrieval_policy_decision=RetrievalPolicyDecision(
            route=RetrievalRoute.NORMAL, query_type="HYBRID", top_k=8, filters_json=None,
            extracted_rule_id=None, reason="test",
        ),
        model="fake-model",
        retrieval_latency_s=0.0,
        generation_latency_s=0.0,
        total_latency_s=0.0,
    )


class FakeStructuredService:
    def __init__(self, events, result=None, exc=None):
        self.events, self.result, self.exc = events, result or _structured_result(), exc
        self.calls: list[tuple] = []

    def execute(self, intent, params=None):
        self.events.append("structured")
        self.calls.append((intent, dict(params or {})))
        if self.exc is not None:
            raise self.exc
        return self.result


class ScriptedOrchestrator:
    """Stands in for RagOrchestrator. It has no structured-generation method
    at all: the Harness must never ask a model to write structured values."""

    def __init__(self, events, rag=None, rag_exc=None):
        self.events = events
        self.rag = rag if rag is not None else _rag_result()
        self.rag_exc = rag_exc
        self.generate_calls: list[tuple] = []

    def generate(self, query, *, retrieval_decision=None, values_shown_separately=False):
        self.events.append("rag")
        self.generate_calls.append((query, values_shown_separately))
        if self.rag_exc is not None:
            raise self.rag_exc
        return self.rag


def _harness(events=None, *, service="default", orchestrator=None, router="default"):
    events = [] if events is None else events
    orchestrator = orchestrator or ScriptedOrchestrator(events)
    if service == "default":
        service = FakeStructuredService(events)
    if router == "default":
        router = RuleBasedSourceRouter()
    harness = AIHarness(orchestrator=orchestrator, structured_query_service=service, source_router=router)
    return harness, orchestrator, service, events


# ---------------------------------------------------------------------------
# AI_SEARCH — existing behavior preserved
# ---------------------------------------------------------------------------


def test_documentary_question_takes_the_existing_ai_search_path():
    harness, orch, service, events = _harness()

    result = harness.ask(DOC_Q)

    assert result.source_route is SourceRoute.AI_SEARCH
    assert result.status is HarnessStatus.AI_SEARCH_OK
    assert orch.generate_calls == [(DOC_Q, False)]  # exactly the old call: natural query, documentary prompt
    assert service.calls == []
    assert result.structured_result is None and result.answer is None
    assert result.rag_result.answer == "regra do ranking [S1]"


def test_existing_zero_evidence_ai_search_behavior_is_unchanged():
    harness, _, service, _ = _harness(orchestrator=ScriptedOrchestrator([], rag=_rag_result(None, evidence=False)))

    result = harness.ask("GOLD-999")

    assert result.status is HarnessStatus.AI_SEARCH_NO_EVIDENCE
    assert service.calls == []


def test_explicit_source_route_overrides_the_router():
    harness, orch, service, _ = _harness()

    result = harness.ask(DATA_Q, source_route=SourceRoute.AI_SEARCH)

    assert result.source_route is SourceRoute.AI_SEARCH
    assert orch.generate_calls == [(DATA_Q, False)]
    assert service.calls == []


def test_without_a_router_every_query_defaults_to_ai_search_as_before():
    harness, orch, service, _ = _harness(router=None)

    result = harness.ask(DATA_Q)

    assert result.source_route is SourceRoute.AI_SEARCH
    assert service.calls == []
    assert orch.generate_calls == [(DATA_Q, False)]


def test_invalid_query_never_reaches_router_service_or_orchestrator():
    router = MagicMock()
    harness, orch, service, _ = _harness(router=router)

    for bad in ("", "   ", None, 123):
        assert harness.ask(bad).status is HarnessStatus.INVALID_REQUEST

    router.route.assert_not_called()
    assert service.calls == [] and orch.generate_calls == []


# ---------------------------------------------------------------------------
# STRUCTURED_DATA
# ---------------------------------------------------------------------------


def test_structured_data_success_returns_the_deterministic_rendering_without_any_model_call():
    harness, orch, service, events = _harness()

    result = harness.ask(DATA_Q)

    assert result.source_route is SourceRoute.STRUCTURED_DATA
    assert result.status is HarnessStatus.STRUCTURED_DATA_OK
    assert service.calls == [(ANALYTICS, {"request": _quantity_ranking()})]
    assert events == ["structured"]  # no retrieval, no generation: nothing else ran
    assert orch.generate_calls == []
    assert result.answer == render_structured_answer(_structured_result())
    assert result.rag_result is None
    assert result.structured_data_available is True


def test_the_final_structured_answer_contains_every_row_exactly_and_in_order():
    harness, _, _, _ = _harness()

    result = harness.ask(DATA_Q)

    lines = [line for line in result.answer.splitlines() if line[:1].isdigit()]
    assert lines == [
        "1. Colete Salva-Vidas 2374 — 1.737 unidades",
        "2. Bateria Náutica 1945 — 1.731 unidades",
        "8. GPS Plotter 6249 — 1.638 unidades",
        "8. Motor de Popa 7678 — 1.638 unidades",
    ]  # ties kept with their shared rank; nothing dropped, nothing invented
    assert result.structured_result.rows == ROWS  # untouched


def test_a_large_structured_result_is_never_truncated_because_no_model_writes_it():
    rows = [TopProductRow(i, i, f"Produto {i:03d}", 9000 - i) for i in range(1, 51)]
    events: list[str] = []
    harness, orch, _, _ = _harness(events, service=FakeStructuredService(events, result=_structured_result(rows)))

    result = harness.ask("Top 50 produtos mais vendidos")

    assert len([line for line in result.answer.splitlines() if line[:1].isdigit()]) == 50
    assert "50. Produto 050 — 8.950 unidades" in result.answer
    assert result.answer.endswith("Fonte: camada Gold — fct_sales + dim_products.")
    assert orch.generate_calls == []


def test_structured_route_passes_a_stated_top_n_to_the_service():
    harness, _, service, _ = _harness()

    harness.ask("Top 5 produtos mais vendidos")

    assert service.calls == [(ANALYTICS, {"request": _quantity_ranking(5)})]


def test_structured_data_empty_result_is_no_data_and_not_answered_from_documents():
    harness, orch, _, events = _harness(service=FakeStructuredService([], result=_structured_result(())))

    result = harness.ask(DATA_Q)

    assert result.status is HarnessStatus.STRUCTURED_DATA_NO_DATA
    assert result.answer is None and result.rag_result is None
    assert result.structured_result.row_count == 0
    assert orch.generate_calls == []


def test_structured_data_executor_failure_is_controlled_and_never_falls_back_to_documents():
    events: list[str] = []
    service = FakeStructuredService(events, exc=StructuredExecutionError("warehouse down"))
    harness, orch, _, _ = _harness(events, service=service)

    result = harness.ask(DATA_Q)

    assert result.status is HarnessStatus.STRUCTURED_DATA_FAILED
    assert result.error == "warehouse down"
    assert result.answer is None and result.rag_result is None
    assert orch.generate_calls == []  # no documentation "answer" substituted


def test_invalid_top_n_is_rejected_by_the_real_service_before_any_sql_runs():
    executed: list[str] = []
    executor = MagicMock()
    executor.execute.side_effect = lambda *a, **k: executed.append("sql") or []
    service = StructuredQueryService(executor, catalog="test_catalog")
    harness, orch, _, _ = _harness(service=service)

    result = harness.ask("Top 0 produtos mais vendidos")

    assert result.status is HarnessStatus.STRUCTURED_DATA_FAILED
    assert executed == []
    assert orch.generate_calls == []


def test_unexpected_service_exception_is_still_contained():
    events: list[str] = []
    harness, orch, _, _ = _harness(events, service=FakeStructuredService(events, exc=RuntimeError("boom")))

    result = harness.ask(DATA_Q)

    assert result.status is HarnessStatus.STRUCTURED_DATA_FAILED and result.error == "boom"


def test_a_rendering_failure_is_contained_like_any_other_structured_failure(monkeypatch):
    def boom(_result):
        raise RuntimeError("renderer bug")

    monkeypatch.setattr("src.harness.harness.render_structured_answer", boom)
    harness, orch, _, _ = _harness()

    result = harness.ask(DATA_Q)

    assert result.status is HarnessStatus.STRUCTURED_DATA_FAILED and result.error == "renderer bug"
    assert result.answer is None and orch.generate_calls == []


# ---------------------------------------------------------------------------
# Unsupported / unavailable
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query", ["Quais são os produtos mais vendidos em março?", "Qual foi o faturamento do mês?", "Quais produtos venderam menos?"]
)
def test_unsupported_structured_question_is_controlled_and_executes_nothing(query):
    harness, orch, service, events = _harness()

    result = harness.ask(query)

    assert result.status is HarnessStatus.STRUCTURED_DATA_UNSUPPORTED
    assert result.error
    assert events == []  # no SQL, no retrieval, no generation
    assert service.calls == [] and orch.generate_calls == []


def test_unsupported_question_is_reported_even_when_no_structured_service_is_configured():
    harness, orch, _, events = _harness(service=None)

    result = harness.ask("Qual foi o faturamento do mês?")

    assert result.status is HarnessStatus.STRUCTURED_DATA_UNSUPPORTED
    assert events == []


def test_explicit_structured_route_without_a_resolvable_intent_is_unsupported():
    harness, _, service, events = _harness(router=None)

    result = harness.ask(DATA_Q, source_route=SourceRoute.STRUCTURED_DATA)

    assert result.status is HarnessStatus.STRUCTURED_DATA_UNSUPPORTED
    assert events == [] and service.calls == []


@pytest.mark.parametrize("query", [DATA_Q, BOTH_Q])
def test_structured_question_without_a_configured_service_fails_closed_without_touching_rag(query):
    harness, orch, _, events = _harness(service=None)

    result = harness.ask(query)

    assert result.status is HarnessStatus.STRUCTURED_DATA_UNAVAILABLE
    assert result.answer is None and result.rag_result is None
    assert events == []  # in particular: no documentation-only answer to a data question


# ---------------------------------------------------------------------------
# BOTH
# ---------------------------------------------------------------------------


def test_both_success_is_the_deterministic_structured_block_plus_the_documentary_explanation():
    harness, orch, service, events = _harness()

    result = harness.ask(BOTH_Q)

    assert result.source_route is SourceRoute.BOTH
    assert result.status is HarnessStatus.BOTH_OK
    assert events == ["structured", "rag"]  # values first, then the documentary explanation
    assert orch.generate_calls == [(BOTH_Q, True)]  # natural query unchanged; explanation-only prompt
    structured_block = render_structured_answer(_structured_result())
    assert result.answer == f"{structured_block}\n\nregra do ranking [S1]"
    assert result.answer.startswith(structured_block)  # the model can neither rewrite nor reorder it
    assert result.rag_result.answer == "regra do ranking [S1]"
    assert result.structured_result.rows == ROWS


def test_both_structured_block_has_no_citation_and_the_explanation_keeps_its_own():
    harness, _, _, _ = _harness()

    result = harness.ask(BOTH_Q)

    structured_block, explanation = result.answer.split("\n\nregra", 1)
    assert "[S" not in structured_block
    assert "[S1]" in explanation


def test_both_with_no_documentary_evidence_returns_the_structured_answer_only():
    events: list[str] = []
    orch = ScriptedOrchestrator(events, rag=_rag_result(None, evidence=False))
    harness, _, _, _ = _harness(events, orchestrator=orch)

    result = harness.ask(BOTH_Q)

    assert result.status is HarnessStatus.BOTH_DEGRADED
    assert result.answer == render_structured_answer(_structured_result())  # no fake explanation added
    assert result.rag_result.sources == []
    assert "no documentary evidence" in result.error
    assert result.structured_result.rows == ROWS


def test_both_with_a_documentary_technical_failure_keeps_the_structured_answer():
    events: list[str] = []
    orch = ScriptedOrchestrator(events, rag_exc=RuntimeError("index unavailable"))
    harness, _, _, _ = _harness(events, orchestrator=orch)

    result = harness.ask(BOTH_Q)

    assert result.status is HarnessStatus.BOTH_DEGRADED
    assert result.answer == render_structured_answer(_structured_result())  # valid data is never discarded
    assert result.rag_result is None  # so the API returns no documentary sources
    assert "index unavailable" in result.error
    assert result.structured_result.rows == ROWS
    assert events == ["structured", "rag"]


def test_both_with_a_structured_failure_never_produces_a_documentation_only_answer():
    events: list[str] = []
    service = FakeStructuredService(events, exc=StructuredExecutionError("warehouse down"))
    harness, orch, _, _ = _harness(events, service=service)

    result = harness.ask(BOTH_Q)

    assert result.status is HarnessStatus.STRUCTURED_DATA_FAILED
    assert result.answer is None and result.rag_result is None
    assert events == ["structured"]  # AI Search / generation never ran


def test_both_with_empty_structured_data_does_not_answer_from_documents_alone():
    events: list[str] = []
    harness, orch, _, _ = _harness(events, service=FakeStructuredService(events, result=_structured_result(())))

    result = harness.ask(BOTH_Q)

    assert result.status is HarnessStatus.STRUCTURED_DATA_NO_DATA
    assert events == ["structured"]


def test_both_where_both_sources_fail_reports_the_structured_failure_first():
    events: list[str] = []
    service = FakeStructuredService(events, exc=StructuredExecutionError("warehouse down"))
    orch = ScriptedOrchestrator(events, rag_exc=RuntimeError("index unavailable"))
    harness, _, _, _ = _harness(events, service=service, orchestrator=orch)

    result = harness.ask(BOTH_Q)

    assert result.status is HarnessStatus.STRUCTURED_DATA_FAILED
    assert result.error == "warehouse down"
    assert events == ["structured"]  # evidence is computed first; nothing is wasted on a doomed answer


def test_both_unsupported_scope_is_unsupported_not_half_answered():
    harness, _, _, events = _harness()

    result = harness.ask("Quais são os produtos mais vendidos em março e qual regra define esse ranking?")

    assert result.status is HarnessStatus.STRUCTURED_DATA_UNSUPPORTED
    assert events == []


# ---------------------------------------------------------------------------
# Real RagOrchestrator wiring (only AI Search + the LLM are faked)
# ---------------------------------------------------------------------------


class RecordingLLM(CustomLLM):
    model_endpoint: str = "fake-model"
    prompts: list = []
    reply: str = "Resposta gerada [S1]"

    @property
    def metadata(self):
        return LLMMetadata(model_name="fake-model", is_chat_model=True)

    def complete(self, prompt: str, formatted: bool = False, **kwargs):
        self.prompts.append(prompt)
        return CompletionResponse(text=self.reply, raw={"usage": None})

    def stream_complete(self, prompt, formatted=False, **kwargs):
        raise NotImplementedError

    def chat(self, messages, **kwargs):
        raise NotImplementedError

    def stream_chat(self, messages, **kwargs):
        raise NotImplementedError


def _chunk(rank=1, chunk_id="c1") -> RetrievedChunk:
    return RetrievedChunk(
        rank=rank, chunk_id=chunk_id, document_id="powerbi_semantic_rules", document_title="Doc",
        canonical_domain="d", section_path="A > B", section_title="B",
        chunk_text="O ranking é denso e considera apenas vendas efetivas.", rule_ids=("GOLD-032",), score=0.9,
    )


def _real_harness(monkeypatch, chunks, *, reply="Resposta gerada [S1]"):
    search_calls: list[str] = []

    def fake_query_index(client, index_name, query_text, **kwargs):
        search_calls.append(query_text)
        return chunks

    monkeypatch.setattr("src.orchestration.rag_pipeline.query_index", fake_query_index)
    llm = RecordingLLM(prompts=[], reply=reply)
    orchestrator = RagOrchestrator(client=MagicMock(), index_name="idx", llm=llm)
    events: list[str] = []
    harness = AIHarness(
        orchestrator=orchestrator,
        structured_query_service=FakeStructuredService(events),
        source_router=RuleBasedSourceRouter(),
    )
    return harness, llm, search_calls


def test_natural_language_query_reaches_ai_search_unchanged_on_the_documentary_route(monkeypatch):
    harness, _, search_calls = _real_harness(monkeypatch, [_chunk()])

    harness.ask(DOC_Q)

    assert search_calls == [DOC_Q]


def test_natural_language_query_reaches_ai_search_unchanged_on_the_both_route(monkeypatch):
    harness, _, search_calls = _real_harness(monkeypatch, [_chunk()])

    harness.ask(BOTH_Q)

    assert search_calls == [BOTH_Q]  # not prefixed with instructions or structured values


def test_structured_route_never_calls_ai_search_or_the_model(monkeypatch):
    harness, llm, search_calls = _real_harness(monkeypatch, [_chunk()])

    result = harness.ask(DATA_Q)

    assert result.status is HarnessStatus.STRUCTURED_DATA_OK
    assert search_calls == []
    assert llm.prompts == []  # the LLM is not invoked merely to reproduce structured facts


def test_structured_answer_is_independent_of_whatever_the_model_would_say(monkeypatch):
    harness, llm, _ = _real_harness(monkeypatch, [_chunk()], reply="Resposta inventada: 999 unidades de nada")

    result = harness.ask(DATA_Q)

    assert result.answer == render_structured_answer(_structured_result())
    assert "inventada" not in result.answer and llm.prompts == []


def test_both_prompt_carries_no_structured_values_and_the_final_answer_carries_all_of_them(monkeypatch):
    harness, llm, search_calls = _real_harness(monkeypatch, [_chunk()], reply="O ranking é denso [S1].")

    result = harness.ask(BOTH_Q)

    (prompt,) = llm.prompts  # one call: the documentary explanation only
    for row in ROWS:
        assert row.product_name not in prompt and str(row.quantity_sold) not in prompt
    assert "Write ONLY the explanation" in prompt
    assert search_calls == [BOTH_Q]
    for row in ROWS:
        assert row.product_name in result.answer  # every row is in the final text, from the renderer
    assert result.answer.endswith("O ranking é denso [S1].")


def test_a_model_that_ignores_the_instructions_cannot_alter_the_structured_block(monkeypatch):
    rogue = "Na verdade o ranking é: 1. Produto Falso — 5 unidades. O critério é denso [S1]."
    harness, _, _ = _real_harness(monkeypatch, [_chunk()], reply=rogue)

    result = harness.ask(BOTH_Q)

    structured_block = render_structured_answer(_structured_result())
    assert result.answer.startswith(structured_block)
    assert result.answer == f"{structured_block}\n\n{rogue}"
    assert result.structured_result.rows == ROWS  # the data itself is untouched


def test_documentary_citations_apply_only_to_documentary_evidence(monkeypatch):
    harness, llm, _ = _real_harness(monkeypatch, [_chunk(1, "c1"), _chunk(2, "c2")], reply="Ranking denso das vendas efetivas [S1].")

    result = harness.ask(BOTH_Q)

    assert result.status is HarnessStatus.BOTH_OK
    # [Sx] labels exist only for the two retrieved documents...
    assert [s["source_label"] for s in result.rag_result.sources] == ["S1", "S2"]
    assert {s["chunk_id"] for s in result.rag_result.sources} == {"c1", "c2"}
    # ...no SQL result was turned into a fake source...
    assert not any("Colete" in str(s) or "1737" in str(s) for s in result.rag_result.sources)
    # ...the citation check over the FINAL answer sees only documentary labels...
    integrity = check_citation_integrity(result.answer, result.rag_result.sources)
    assert integrity.status is CitationIntegrityStatus.VALID and integrity.cited_labels == ("S1",)
    # ...and none of them sits inside the structured block.
    structured_block = render_structured_answer(_structured_result())
    assert "[S" not in structured_block
    assert not hasattr(result.structured_result, "source_label")


def test_both_with_no_documentary_evidence_degrades_without_calling_the_model(monkeypatch):
    harness, llm, _ = _real_harness(monkeypatch, [])

    result = harness.ask(BOTH_Q)

    assert result.status is HarnessStatus.BOTH_DEGRADED
    assert result.answer == render_structured_answer(_structured_result())
    assert result.rag_result.sources == [] and result.rag_result.evidence_available is False
    assert llm.prompts == []  # no fake explanation, no model call


def test_both_when_retrieval_raises_still_returns_the_structured_answer(monkeypatch):
    def broken_query_index(*args, **kwargs):
        raise RuntimeError("vector index unreachable")

    monkeypatch.setattr("src.orchestration.rag_pipeline.query_index", broken_query_index)
    llm = RecordingLLM(prompts=[])
    harness = AIHarness(
        orchestrator=RagOrchestrator(client=MagicMock(), index_name="idx", llm=llm),
        structured_query_service=FakeStructuredService([]),
        source_router=RuleBasedSourceRouter(),
    )

    result = harness.ask(BOTH_Q)

    assert result.status is HarnessStatus.BOTH_DEGRADED
    assert result.answer == render_structured_answer(_structured_result())
    assert result.rag_result is None and llm.prompts == []


def test_both_when_the_explanation_generation_raises_still_returns_the_structured_answer(monkeypatch):
    class FailingLLM(RecordingLLM):
        def complete(self, prompt, formatted=False, **kwargs):
            raise RuntimeError("serving endpoint down")

    monkeypatch.setattr("src.orchestration.rag_pipeline.query_index", lambda *a, **k: [_chunk()])
    harness = AIHarness(
        orchestrator=RagOrchestrator(client=MagicMock(), index_name="idx", llm=FailingLLM(prompts=[])),
        structured_query_service=FakeStructuredService([]),
        source_router=RuleBasedSourceRouter(),
    )

    result = harness.ask(BOTH_Q)

    assert result.status is HarnessStatus.BOTH_DEGRADED
    assert result.answer == render_structured_answer(_structured_result())
    assert "serving endpoint down" in result.error


def test_both_result_exposes_the_retrieval_decision_the_tracing_layer_reads(monkeypatch):
    harness, _, _ = _real_harness(monkeypatch, [_chunk()])

    result = harness.ask(BOTH_Q)

    decision = result.rag_result.retrieval_policy_decision
    assert decision.route.value in {"NORMAL", "CROSS_SOURCE", "EXACT_RULE_ID"}
    assert isinstance(result, HarnessResult)


# ---------------------------------------------------------------------------
# Layering guards
# ---------------------------------------------------------------------------


def test_route_decision_can_be_supplied_by_any_router_implementation():
    class FixedRouter:
        def route(self, query):
            return RouteDecision(SourceRoute.STRUCTURED_DATA, structured_intent=TOP, structured_params={"top_n": 3})

    harness, _, service, _ = _harness(router=FixedRouter())

    result = harness.ask("qualquer texto")

    assert result.status is HarnessStatus.STRUCTURED_DATA_OK
    assert service.calls == [(TOP, {"top_n": 3})]


def test_structured_statuses_are_distinct_from_the_legacy_placeholder_statuses():
    new = {
        "STRUCTURED_DATA_OK", "BOTH_OK", "BOTH_DEGRADED", "STRUCTURED_DATA_NO_DATA",
        "STRUCTURED_DATA_FAILED", "STRUCTURED_DATA_UNSUPPORTED",
    }
    assert new <= {s.value for s in HarnessStatus}
    assert {"STRUCTURED_DATA_UNAVAILABLE", "STRUCTURED_DATA_FETCHED_NO_GENERATION", "BOTH_PARTIAL"} <= {s.value for s in HarnessStatus}
