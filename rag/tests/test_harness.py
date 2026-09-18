"""Unit tests for src.harness.harness — the AI Harness Phase 1 boundary.
Only in-memory fakes: no Databricks network calls, no AI Search calls, no
MLflow calls, no real ML tables. Mirrors the fake-injection conventions
already established in test_rag_pipeline.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from llama_index.core.base.llms.types import CompletionResponse, LLMMetadata
from llama_index.core.llms import CustomLLM

from src.harness.harness import (
    AIHarness,
    DEFAULT_SOURCE_ROUTE,
    HarnessResult,
    HarnessStatus,
    SourceRoute,
    StructuredEvidence,
)
from src.orchestration.rag_pipeline import RagOrchestrator
from src.retrieval.ai_search import RetrievedChunk


def _chunk(rank=1, chunk_id="c1", **overrides) -> RetrievedChunk:
    defaults = dict(
        rank=rank,
        chunk_id=chunk_id,
        document_id="doc-1",
        document_title="Title",
        canonical_domain="domain",
        section_path="A > B",
        section_title="B",
        chunk_text="some text",
        rule_ids=(),
        score=0.9,
    )
    defaults.update(overrides)
    return RetrievedChunk(**defaults)


class _FakeLLM(CustomLLM):
    model_endpoint: str = "fake-model"

    @property
    def metadata(self):
        return LLMMetadata(model_name="fake-model", is_chat_model=True)

    def complete(self, prompt: str, formatted: bool = False, **kwargs):
        return CompletionResponse(text="fake grounded answer [S1]", raw={"usage": None})

    def stream_complete(self, prompt, formatted=False, **kwargs):
        raise NotImplementedError

    def chat(self, messages, **kwargs):
        raise NotImplementedError

    def stream_chat(self, messages, **kwargs):
        raise NotImplementedError


def _make_real_orchestrator(monkeypatch, chunks) -> RagOrchestrator:
    """A real RagOrchestrator with only the AI Search network call faked
    (exact pattern from test_rag_pipeline.py) — used where a test needs a
    genuine, unmodified OrchestrationResult, not a mock."""

    def fake_query_index(client, index_name, query_text, **kwargs):
        return chunks

    monkeypatch.setattr("src.orchestration.rag_pipeline.query_index", fake_query_index)
    return RagOrchestrator(client=MagicMock(), index_name="idx", llm=_FakeLLM())


class _CountingOrchestrator:
    """A bare stand-in for RagOrchestrator (not a subclass) that only
    records how many times .generate() was called — used where a test
    cares about call count/route, not about a realistic answer."""

    def __init__(self, result=None, exc: Exception | None = None):
        self.calls: list[str] = []
        self._result = result
        self._exc = exc

    def generate(self, query, *, retrieval_decision=None):
        self.calls.append(query)
        if self._exc is not None:
            raise self._exc
        return self._result


class _FakeProvider:
    """Deliberately NOT a subclass of StructuredDataProvider — proves the
    Protocol is satisfied structurally, matching how fakes are built
    elsewhere in this repo (e.g. test_rag_pipeline.py's _FakeLLM only
    subclasses CustomLLM because LlamaIndex requires it; this Protocol
    requires nothing)."""

    def __init__(self, available: bool, evidence=()):
        self._available = available
        self._evidence = evidence
        self.fetch_calls: list[str] = []

    def is_available(self) -> bool:
        return self._available

    def fetch(self, query: str):
        self.fetch_calls.append(query)
        return self._evidence


# ---------------------------------------------------------------------------
# 0. Request validation (Phase 1.1) — invalid queries never reach
# RagOrchestrator or StructuredDataProvider, and never raise.
# ---------------------------------------------------------------------------


def test_empty_query_is_invalid_request():
    orch = _CountingOrchestrator()
    harness = AIHarness(orchestrator=orch)

    result = harness.ask("")

    assert result.status is HarnessStatus.INVALID_REQUEST
    assert result.rag_result is None
    assert result.error is not None
    assert result.query == ""  # not silently modified


def test_whitespace_only_query_is_invalid_request():
    orch = _CountingOrchestrator()
    harness = AIHarness(orchestrator=orch)

    result = harness.ask("   \n\t  ")

    assert result.status is HarnessStatus.INVALID_REQUEST
    assert result.rag_result is None
    assert result.query == "   \n\t  "  # not stripped/rewritten


def test_invalid_query_never_calls_rag_orchestrator():
    orch = _CountingOrchestrator()
    harness = AIHarness(orchestrator=orch)

    for route in (SourceRoute.AI_SEARCH, SourceRoute.STRUCTURED_DATA, SourceRoute.BOTH):
        harness.ask("", source_route=route)

    assert orch.calls == []


def test_invalid_query_never_calls_structured_data_provider():
    orch = _CountingOrchestrator()
    provider = _FakeProvider(available=True, evidence=())
    harness = AIHarness(orchestrator=orch, structured_data_provider=provider)

    for route in (SourceRoute.AI_SEARCH, SourceRoute.STRUCTURED_DATA, SourceRoute.BOTH):
        harness.ask("   ", source_route=route)

    assert provider.fetch_calls == []


def test_invalid_query_does_not_raise():
    harness = AIHarness(orchestrator=_CountingOrchestrator())
    # None is not a valid query either (not a string) — must still return
    # a controlled result, never an exception.
    result = harness.ask(None)  # type: ignore[arg-type]
    assert result.status is HarnessStatus.INVALID_REQUEST
    assert result.query is None  # not coerced into a string


# ---------------------------------------------------------------------------
# 1-2. Default route / explicit AI_SEARCH calls RagOrchestrator exactly once
# ---------------------------------------------------------------------------


def test_default_route_is_ai_search():
    assert DEFAULT_SOURCE_ROUTE is SourceRoute.AI_SEARCH

    orch = _CountingOrchestrator(result=MagicMock(evidence_available=True))
    harness = AIHarness(orchestrator=orch)

    result = harness.ask("uma pergunta qualquer")

    assert result.source_route is SourceRoute.AI_SEARCH
    assert len(orch.calls) == 1


def test_explicit_ai_search_calls_orchestrator_exactly_once():
    orch = _CountingOrchestrator(result=MagicMock(evidence_available=True))
    harness = AIHarness(orchestrator=orch)

    harness.ask("uma pergunta qualquer", source_route=SourceRoute.AI_SEARCH)

    assert len(orch.calls) == 1
    assert orch.calls[0] == "uma pergunta qualquer"


# ---------------------------------------------------------------------------
# 3-4. OrchestrationResult preserved / zero-evidence passthrough
# ---------------------------------------------------------------------------


def test_orchestration_result_is_preserved_unmodified(monkeypatch):
    real_orchestrator = _make_real_orchestrator(monkeypatch, [_chunk(1, "c1"), _chunk(2, "c2")])
    expected = real_orchestrator.generate("uma pergunta qualquer")

    harness = AIHarness(orchestrator=real_orchestrator)
    result = harness.ask("uma pergunta qualquer", source_route=SourceRoute.AI_SEARCH)

    assert result.status is HarnessStatus.AI_SEARCH_OK
    assert result.rag_result is not None
    assert result.rag_result.answer == expected.answer
    assert result.rag_result.sources == expected.sources
    assert result.rag_result.evidence_available == expected.evidence_available
    assert result.rag_result.retrieval_policy_decision == expected.retrieval_policy_decision
    assert result.structured_evidence == ()
    assert result.structured_data_available is False


def test_zero_rag_evidence_passes_through_correctly(monkeypatch):
    real_orchestrator = _make_real_orchestrator(monkeypatch, [])
    harness = AIHarness(orchestrator=real_orchestrator)

    result = harness.ask("GOLD-999", source_route=SourceRoute.AI_SEARCH)

    assert result.status is HarnessStatus.AI_SEARCH_NO_EVIDENCE
    assert result.rag_result is not None
    assert result.rag_result.evidence_available is False
    assert result.rag_result.answer is None
    assert result.rag_result.sources == []


# ---------------------------------------------------------------------------
# 5-6. STRUCTURED_DATA unavailable (no provider / provider unavailable),
# never calling RAG
# ---------------------------------------------------------------------------


def test_structured_data_with_no_provider_returns_unavailable():
    orch = _CountingOrchestrator()
    harness = AIHarness(orchestrator=orch, structured_data_provider=None)

    result = harness.ask("qual a previsão de demanda?", source_route=SourceRoute.STRUCTURED_DATA)

    assert result.status is HarnessStatus.STRUCTURED_DATA_UNAVAILABLE
    assert result.rag_result is None
    assert result.structured_evidence == ()
    assert result.structured_data_available is False
    assert len(orch.calls) == 0


def test_structured_data_with_unavailable_provider_never_calls_rag():
    orch = _CountingOrchestrator()
    provider = _FakeProvider(available=False)
    harness = AIHarness(orchestrator=orch, structured_data_provider=provider)

    result = harness.ask("qual a previsão de demanda?", source_route=SourceRoute.STRUCTURED_DATA)

    assert result.status is HarnessStatus.STRUCTURED_DATA_UNAVAILABLE
    assert len(orch.calls) == 0
    assert provider.fetch_calls == []


def test_structured_data_with_available_provider_fetches_but_does_not_generate():
    orch = _CountingOrchestrator()
    evidence = (StructuredEvidence(source_id="x", description="y", content="z"),)
    provider = _FakeProvider(available=True, evidence=evidence)
    harness = AIHarness(orchestrator=orch, structured_data_provider=provider)

    result = harness.ask("qual a previsão de demanda?", source_route=SourceRoute.STRUCTURED_DATA)

    assert result.status is HarnessStatus.STRUCTURED_DATA_FETCHED_NO_GENERATION
    assert result.structured_evidence == evidence
    assert result.structured_data_available is True
    assert result.rag_result is None
    assert len(orch.calls) == 0  # STRUCTURED_DATA alone never touches RAG


# ---------------------------------------------------------------------------
# 7-8. BOTH with unavailable structured provider returns an explicit
# partial result
# ---------------------------------------------------------------------------


def test_both_with_unavailable_structured_provider_returns_partial(monkeypatch):
    real_orchestrator = _make_real_orchestrator(monkeypatch, [_chunk(1, "c1")])
    provider = _FakeProvider(available=False)
    harness = AIHarness(orchestrator=real_orchestrator, structured_data_provider=provider)

    result = harness.ask("Ticket Médio no Gold e Power BI?", source_route=SourceRoute.BOTH)

    assert result.status is HarnessStatus.BOTH_PARTIAL
    assert result.rag_result is not None
    assert result.rag_result.evidence_available is True


def test_both_partial_result_clearly_indicates_structured_data_unavailable(monkeypatch):
    real_orchestrator = _make_real_orchestrator(monkeypatch, [_chunk(1, "c1")])
    harness = AIHarness(orchestrator=real_orchestrator, structured_data_provider=None)

    result = harness.ask("Ticket Médio no Gold e Power BI?", source_route=SourceRoute.BOTH)

    assert result.status is HarnessStatus.BOTH_PARTIAL
    assert result.structured_data_available is False
    assert result.structured_evidence == ()


def test_both_with_available_structured_provider_still_partial_but_includes_evidence(monkeypatch):
    real_orchestrator = _make_real_orchestrator(monkeypatch, [_chunk(1, "c1")])
    evidence = (StructuredEvidence(source_id="x", description="y", content="z"),)
    provider = _FakeProvider(available=True, evidence=evidence)
    harness = AIHarness(orchestrator=real_orchestrator, structured_data_provider=provider)

    result = harness.ask("Ticket Médio no Gold e Power BI?", source_route=SourceRoute.BOTH)

    # Never presented as a complete/merged answer in Phase 1 — see
    # HarnessStatus.BOTH_PARTIAL docstring.
    assert result.status is HarnessStatus.BOTH_PARTIAL
    assert result.structured_data_available is True
    assert result.structured_evidence == evidence
    assert result.rag_result is not None


# ---------------------------------------------------------------------------
# 9. RagOrchestrator runtime/execution exception becomes controlled failure
# ---------------------------------------------------------------------------


def test_rag_orchestrator_exception_becomes_rag_execution_failed():
    orch = _CountingOrchestrator(exc=RuntimeError("serving endpoint timed out"))
    harness = AIHarness(orchestrator=orch)

    result = harness.ask("uma pergunta qualquer", source_route=SourceRoute.AI_SEARCH)

    assert result.status is HarnessStatus.RAG_EXECUTION_FAILED
    assert result.rag_result is None
    assert result.error == "serving endpoint timed out"


def test_both_rag_orchestrator_exception_becomes_rag_execution_failed():
    orch = _CountingOrchestrator(exc=RuntimeError("boom"))
    provider = _FakeProvider(available=True, evidence=())
    harness = AIHarness(orchestrator=orch, structured_data_provider=provider)

    result = harness.ask("uma pergunta qualquer", source_route=SourceRoute.BOTH)

    assert result.status is HarnessStatus.RAG_EXECUTION_FAILED
    assert result.error == "boom"
    # The failure short-circuits before structured data is even fetched.
    assert provider.fetch_calls == []


def test_no_generation_failed_identifier_remains_anywhere():
    """Phase 1.1: GENERATION_FAILED was renamed to RAG_EXECUTION_FAILED
    because RagOrchestrator.generate covers retrieval, AI Search
    transport, LlamaIndex orchestration, and generation — not just
    generation. Guards against the old name silently creeping back in."""
    assert not hasattr(HarnessStatus, "GENERATION_FAILED")
    for member in HarnessStatus:
        assert member.name != "GENERATION_FAILED"
        assert member.value != "GENERATION_FAILED"

    path = Path(__file__).resolve().parents[1] / "src" / "harness" / "harness.py"
    assert "GENERATION_FAILED" not in path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 10. Fake StructuredDataProvider structurally satisfies the Protocol
# ---------------------------------------------------------------------------


def test_fake_provider_structurally_satisfies_protocol_without_inheriting_it():
    from src.harness.harness import StructuredDataProvider

    provider = _FakeProvider(available=True, evidence=(StructuredEvidence("a", "b", "c"),))
    assert StructuredDataProvider not in type(provider).__mro__  # not declared as a subclass
    assert provider.is_available() is True
    assert list(provider.fetch("q")) == [StructuredEvidence("a", "b", "c")]

    # And it works end-to-end through the Harness, which only relies on
    # structural typing (duck typing), not inheritance.
    orch = _CountingOrchestrator()
    harness = AIHarness(orchestrator=orch, structured_data_provider=provider)
    result = harness.ask("q", source_route=SourceRoute.STRUCTURED_DATA)
    assert result.status is HarnessStatus.STRUCTURED_DATA_FETCHED_NO_GENERATION


# ---------------------------------------------------------------------------
# 11. No speculative ML table/catalog/schema names in production code
# ---------------------------------------------------------------------------


def test_no_speculative_ml_identifiers_in_harness_source():
    path = Path(__file__).resolve().parents[1] / "src" / "harness" / "harness.py"
    text = path.read_text(encoding="utf-8")

    banned_identifiers = [
        # personal/dev catalogs and real table names observed in the
        # separate ML Integration Contract Review — none belong here yet.
        "dev_adriano_takata",
        "dev_natalia_correia",
        "prod_lh_nautical",
        "demand_forecast_rolling_3m",
        "dataset_demand_full",
        "demand_forecast_produtos",
        "fct_sales",
        # concrete SQL/Spark execution surface — this module must stay a
        # transport-free interface boundary.
        "spark.sql",
        "spark.table",
        "DeltaTable",
        "CREATE TABLE",
        "SELECT ",
    ]
    for identifier in banned_identifiers:
        assert identifier not in text, f"found speculative/production identifier {identifier!r} in harness.py"
