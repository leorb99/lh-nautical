"""Unit tests for src.harness.tracing — the Phase 2B MLflow tracing
boundary. Uses MLflow's default local (offline) tracking, pointed at a
pytest-managed temp directory — no live Databricks/MLflow backend
required, matching Phase 2B Section 7. Reuses the same fake-injection
conventions as test_harness.py/test_rag_pipeline.py.

MLflow is intentionally not a dependency of the shared dbt/ingestion venv
(see the Phase 2A/2B reports — installing it there upgrades databricks-sdk
and breaks dbt-databricks' pin). This whole module is skipped, not failed,
when mlflow isn't importable, so the shared venv's regression run stays
green without it; a RAG/App-scoped environment that does have mlflow
installed runs these tests fully."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

mlflow = pytest.importorskip("mlflow", reason="mlflow is not part of the shared dbt/ingestion venv (see Phase 2A/2B reports)")

from llama_index.core.base.llms.types import CompletionResponse, LLMMetadata
from llama_index.core.llms import CustomLLM

from src.harness.harness import AIHarness, HarnessStatus, SourceRoute
from src.harness.tracing import traced_ask
from src.orchestration.rag_pipeline import RagOrchestrator
from src.retrieval.ai_search import RetrievedChunk

_HARNESS_PATH = Path(__file__).resolve().parents[1] / "src" / "harness" / "harness.py"
_TRACING_PATH = Path(__file__).resolve().parents[1] / "src" / "harness" / "tracing.py"


@pytest.fixture(autouse=True)
def _isolated_local_tracking(tmp_path):
    """Every test gets its own throwaway local MLflow tracking store —
    no network call, no shared state between tests, nothing left behind
    in the repo working directory."""
    mlflow.set_tracking_uri(f"sqlite:///{tmp_path}/mlflow.db")
    mlflow.set_experiment("test_tracing")
    yield


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
    def fake_query_index(client, index_name, query_text, **kwargs):
        return chunks

    monkeypatch.setattr("src.orchestration.rag_pipeline.query_index", fake_query_index)
    return RagOrchestrator(client=MagicMock(), index_name="idx", llm=_FakeLLM())


class _CountingOrchestrator:
    def __init__(self, result=None, exc: Exception | None = None):
        self.calls: list[str] = []
        self._result = result
        self._exc = exc

    def generate(self, query, *, retrieval_decision=None):
        self.calls.append(query)
        if self._exc is not None:
            raise self._exc
        return self._result


def _last_trace():
    trace_id = mlflow.get_last_active_trace_id()
    mlflow.flush_trace_async_logging()
    return mlflow.get_trace(trace_id)


def _root_span(trace):
    # The one manual span tracing.py creates is identified by being the
    # trace's root (no parent), not by being the trace's only span:
    # _make_real_orchestrator() builds a genuine RagOrchestrator/
    # PolicyRetriever/CustomLLM, so mlflow.llama_index.autolog() (Phase
    # 2A/2B, see tracing.py's own module docstring) legitimately nests
    # PolicyRetriever.retrieve/_retrieve and DatabricksLlamaLLM.complete
    # spans underneath it — exactly the intended production trace shape,
    # already independently confirmed against the real deployed App
    # (3-4 spans per trace there too). Child-span count/shape is
    # autolog/LlamaIndex framework behavior, not this module's
    # responsibility, and is not what these root-span tests assert on.
    roots = [span for span in trace.data.spans if span.parent_id is None]
    assert len(roots) == 1, (
        f"expected exactly one root span, found {len(roots)}: "
        f"{[span.name for span in roots]}"
    )
    root = roots[0]
    assert root.name == "AIHarness.ask"
    return root


def _root_span_attributes(trace):
    return _root_span(trace).attributes


# ---------------------------------------------------------------------------
# 1. AIHarness remains MLflow-independent
# ---------------------------------------------------------------------------


def test_harness_module_never_imports_mlflow():
    # harness.py's own docstring mentions "MLflow tracing" in prose (as
    # explicit future-work scope) — that's fine; what must never appear
    # is an actual import coupling the class to the framework.
    tree = ast.parse(_HARNESS_PATH.read_text(encoding="utf-8"))
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    assert not any(m == "mlflow" or m.startswith("mlflow.") for m in imported_modules)


# ---------------------------------------------------------------------------
# 2. tracing wrapper calls AIHarness exactly once
# ---------------------------------------------------------------------------


def test_traced_ask_calls_harness_exactly_once():
    orch = _CountingOrchestrator(result=MagicMock(evidence_available=True))
    harness = AIHarness(orchestrator=orch)

    traced_ask(harness, "uma pergunta qualquer")

    assert len(orch.calls) == 1


# ---------------------------------------------------------------------------
# Root span payload — explicit set_inputs()/set_outputs() must override the
# decorator's default full-argument/full-return-value auto-capture.
# ---------------------------------------------------------------------------


def test_root_inputs_contain_exactly_query_and_source_route(monkeypatch):
    real_orchestrator = _make_real_orchestrator(monkeypatch, [_chunk(1, "c1")])
    harness = AIHarness(orchestrator=real_orchestrator)

    traced_ask(harness, "uma pergunta qualquer", source_route=SourceRoute.AI_SEARCH)

    inputs = _root_span(_last_trace()).inputs
    assert inputs == {"query": "uma pergunta qualquer", "source_route": "AI_SEARCH"}


def test_root_inputs_do_not_contain_harness_object_or_result_payload(monkeypatch):
    real_orchestrator = _make_real_orchestrator(monkeypatch, [_chunk(1, "c1")])
    harness = AIHarness(orchestrator=real_orchestrator)

    traced_ask(harness, "uma pergunta qualquer")

    inputs = _root_span(_last_trace()).inputs
    assert "harness" not in inputs
    assert "rag_result" not in inputs
    assert "AIHarness object" not in str(inputs)


def test_root_outputs_contain_exactly_source_route_and_harness_status(monkeypatch):
    real_orchestrator = _make_real_orchestrator(monkeypatch, [_chunk(1, "c1")])
    harness = AIHarness(orchestrator=real_orchestrator)

    traced_ask(harness, "uma pergunta qualquer", source_route=SourceRoute.AI_SEARCH)

    outputs = _root_span(_last_trace()).outputs
    assert outputs == {"source_route": "AI_SEARCH", "harness_status": "AI_SEARCH_OK"}


def test_root_outputs_do_not_contain_lower_level_rag_payload(monkeypatch):
    real_orchestrator = _make_real_orchestrator(monkeypatch, [_chunk(1, "c1", score=0.42)])
    harness = AIHarness(orchestrator=real_orchestrator)

    traced_ask(harness, "uma pergunta qualquer")

    outputs = _root_span(_last_trace()).outputs
    for forbidden_key in ("answer", "sources", "usage", "retrieval_latency_s", "generation_latency_s",
                          "total_latency_s", "retrieval_policy_decision", "rag_result"):
        assert forbidden_key not in outputs
    # And not merely absent as top-level keys — the actual answer/score
    # values must not leak into the (small) outputs payload either.
    assert "fake grounded answer" not in str(outputs)
    assert "0.42" not in str(outputs)


def test_traced_ask_returns_original_harness_result_unchanged(monkeypatch):
    real_orchestrator = _make_real_orchestrator(monkeypatch, [_chunk(1, "c1")])
    harness = AIHarness(orchestrator=real_orchestrator)

    direct_result = harness.ask("uma pergunta qualquer", source_route=SourceRoute.AI_SEARCH)
    traced_result = traced_ask(harness, "uma pergunta qualquer", source_route=SourceRoute.AI_SEARCH)

    # Same shape/content as calling harness.ask() directly — the wrapper
    # only observes, it does not transform the result.
    assert traced_result.query == direct_result.query
    assert traced_result.source_route == direct_result.source_route
    assert traced_result.status == direct_result.status
    assert traced_result.rag_result.answer == direct_result.rag_result.answer
    assert traced_result.rag_result.sources == direct_result.rag_result.sources


# ---------------------------------------------------------------------------
# 3. Successful RAG result produces expected request-level attributes
# ---------------------------------------------------------------------------


def test_successful_ai_search_produces_expected_attributes(monkeypatch):
    real_orchestrator = _make_real_orchestrator(monkeypatch, [_chunk(1, "c1"), _chunk(2, "c2")])
    harness = AIHarness(orchestrator=real_orchestrator)

    result = traced_ask(harness, "uma pergunta qualquer", source_route=SourceRoute.AI_SEARCH)
    assert result.status is HarnessStatus.AI_SEARCH_OK

    attrs = _root_span_attributes(_last_trace())
    assert attrs["source_route"] == "AI_SEARCH"
    assert attrs["harness_status"] == "AI_SEARCH_OK"
    assert attrs["retrieval_route"] == "NORMAL"
    assert attrs["top_k"] == 8
    assert attrs["evidence_count"] == 2


# ---------------------------------------------------------------------------
# 4. Zero evidence records evidence_count=0
# ---------------------------------------------------------------------------


def test_zero_evidence_records_evidence_count_zero(monkeypatch):
    real_orchestrator = _make_real_orchestrator(monkeypatch, [])
    harness = AIHarness(orchestrator=real_orchestrator)

    result = traced_ask(harness, "GOLD-999", source_route=SourceRoute.AI_SEARCH)
    assert result.status is HarnessStatus.AI_SEARCH_NO_EVIDENCE

    attrs = _root_span_attributes(_last_trace())
    assert attrs["harness_status"] == "AI_SEARCH_NO_EVIDENCE"
    assert attrs["evidence_count"] == 0
    # Distinguishable from a real execution failure at the attribute level:
    assert attrs["harness_status"] != "RAG_EXECUTION_FAILED"


# ---------------------------------------------------------------------------
# 5. Invalid request does not fabricate retrieval attributes
# ---------------------------------------------------------------------------


def test_invalid_request_does_not_fabricate_retrieval_attributes():
    orch = _CountingOrchestrator()
    harness = AIHarness(orchestrator=orch)

    result = traced_ask(harness, "   ")
    assert result.status is HarnessStatus.INVALID_REQUEST
    assert len(orch.calls) == 0

    attrs = _root_span_attributes(_last_trace())
    assert attrs["harness_status"] == "INVALID_REQUEST"
    assert "source_route" in attrs  # resolved route is genuinely known
    assert "retrieval_route" not in attrs
    assert "top_k" not in attrs
    assert "evidence_count" not in attrs


# ---------------------------------------------------------------------------
# 6. RAG_EXECUTION_FAILED does not fabricate retrieval attributes
# ---------------------------------------------------------------------------


def test_rag_execution_failed_does_not_fabricate_retrieval_attributes():
    orch = _CountingOrchestrator(exc=RuntimeError("serving endpoint timed out"))
    harness = AIHarness(orchestrator=orch)

    result = traced_ask(harness, "uma pergunta qualquer", source_route=SourceRoute.AI_SEARCH)
    assert result.status is HarnessStatus.RAG_EXECUTION_FAILED

    attrs = _root_span_attributes(_last_trace())
    assert attrs["harness_status"] == "RAG_EXECUTION_FAILED"
    assert attrs["source_route"] == "AI_SEARCH"
    assert "retrieval_route" not in attrs
    assert "top_k" not in attrs
    assert "evidence_count" not in attrs


# ---------------------------------------------------------------------------
# 7. Lower-level latency/token/model information is NOT manually duplicated
# ---------------------------------------------------------------------------


def test_tracing_module_sets_only_the_five_documented_attributes():
    text = _TRACING_PATH.read_text(encoding="utf-8")
    set_keys = set(re.findall(r'set_attribute\(\s*"([^"]+)"', text))
    assert set_keys == {"source_route", "harness_status", "retrieval_route", "top_k", "evidence_count"}


def test_tracing_module_never_sets_lower_level_attributes():
    text = _TRACING_PATH.read_text(encoding="utf-8")
    set_calls = re.findall(r'set_attribute\(\s*"([^"]+)"', text)
    banned_substrings = ("token", "latency", "prompt", "completion", "chunk", "score", "model")
    for key in set_calls:
        for banned in banned_substrings:
            assert banned not in key.lower(), f"attribute {key!r} looks like a lower-level value autolog already provides"
