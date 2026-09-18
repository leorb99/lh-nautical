"""Unit/controller tests for rag/app.py — the FastAPI App backend
(Phase 4B). No Databricks, no MLflow backend, no live network call: the
`traced_ask` runtime boundary is faked at the module level, matching the
fake-injection conventions already used throughout test_harness.py/
test_tracing.py/test_citations.py.

Requires the App-scoped dependencies (fastapi, mlflow-tracing) to be
importable — this whole module is skipped, not failed, when they aren't,
so the shared dbt venv's regression run stays green without them."""

from __future__ import annotations

import copy

import pytest

fastapi_testclient = pytest.importorskip(
    "fastapi.testclient", reason="fastapi is App-scoped, not part of the shared dbt/ingestion venv"
)
pytest.importorskip("mlflow", reason="mlflow-tracing is App-scoped, not part of the shared dbt/ingestion venv")

import os
import sys
from pathlib import Path

# app.py requires RAG_CATALOG at import time (no personal-path fallback —
# see app.py's own module docstring/comments) — set a portable test value
# before importing it, exactly once for this whole test module.
os.environ.setdefault("RAG_CATALOG", "test_catalog")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as app_module  # noqa: E402  (import after sys.path/env setup above)
from src.harness.harness import HarnessResult, HarnessStatus, SourceRoute  # noqa: E402
from src.orchestration.rag_pipeline import OrchestrationResult  # noqa: E402
from src.retrieval.policy import RetrievalPolicyDecision, RetrievalRoute  # noqa: E402

TestClient = fastapi_testclient.TestClient


def _source(label, **overrides):
    defaults = dict(
        source_label=label,
        chunk_id=f"chunk-{label}",
        document_id="doc-1",
        document_title="Title",
        section_path="A > B",
        section_title="B",
        rule_ids=[],
        rank=1,
        score=0.9,
    )
    defaults.update(overrides)
    return defaults


def _decision(route=RetrievalRoute.NORMAL, top_k=8):
    return RetrievalPolicyDecision(
        route=route,
        query_type="HYBRID",
        top_k=top_k,
        filters_json=None,
        extracted_rule_id=None,
        reason="test",
    )


def _rag_result(answer, sources, evidence_available=True, decision=None):
    return OrchestrationResult(
        query="q",
        answer=answer,
        evidence_available=evidence_available,
        sources=sources,
        retrieval_policy_decision=decision or _decision(),
        model="fake-model",
        retrieval_latency_s=0.01,
        generation_latency_s=0.02 if answer is not None else None,
        total_latency_s=0.03,
        usage=None,
    )


def _harness_result(status, rag_result=None, error=None, source_route=SourceRoute.AI_SEARCH):
    return HarnessResult(
        query="q",
        source_route=source_route,
        status=status,
        rag_result=rag_result,
        error=error,
    )


class _FakeTracedAsk:
    """Records calls; returns a pre-configured HarnessResult. Installed in
    place of the real traced_ask/harness so no mlflow trace or RagOrchestrator
    call happens in these tests."""

    def __init__(self, result: HarnessResult):
        self.result = result
        self.calls: list[tuple[object, str]] = []

    def __call__(self, harness, query, *, source_route=None):
        self.calls.append((harness, query))
        return self.result


@pytest.fixture
def client():
    return TestClient(app_module.app)


def _install_fake(monkeypatch, result: HarnessResult) -> _FakeTracedAsk:
    fake = _FakeTracedAsk(result)
    monkeypatch.setattr(app_module, "traced_ask", fake)
    return fake


# ---------------------------------------------------------------------------
# 1. AI_SEARCH_OK
# ---------------------------------------------------------------------------


def test_ai_search_ok_returns_200_with_full_body(client, monkeypatch):
    sources = [_source("S1")]
    result = _harness_result(HarnessStatus.AI_SEARCH_OK, rag_result=_rag_result("De acordo com [S1].", sources))
    _install_fake(monkeypatch, result)

    resp = client.post("/api/ask", json={"query": "Como funciona?"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "AI_SEARCH_OK"
    assert body["answer"] == "De acordo com [S1]."
    assert len(body["sources"]) == 1
    assert body["sources"][0]["source_label"] == "S1"
    assert body["message"] is None


# ---------------------------------------------------------------------------
# 2. AI_SEARCH_OK with VALID citations
# ---------------------------------------------------------------------------


def test_ai_search_ok_with_valid_citations(client, monkeypatch):
    sources = [_source("S1"), _source("S2")]
    result = _harness_result(
        HarnessStatus.AI_SEARCH_OK, rag_result=_rag_result("Conforme [S1, S2].", sources)
    )
    _install_fake(monkeypatch, result)

    resp = client.post("/api/ask", json={"query": "q"})

    body = resp.json()
    assert body["citation_integrity"]["status"] == "VALID"
    assert body["citation_integrity"]["cited_labels"] == ["S1", "S2"]
    assert body["citation_integrity"]["unknown_labels"] == []


# ---------------------------------------------------------------------------
# 3. AI_SEARCH_OK with INVALID citation label
# ---------------------------------------------------------------------------


def test_ai_search_ok_with_invalid_citation_label(client, monkeypatch):
    sources = [_source("S1")]
    result = _harness_result(HarnessStatus.AI_SEARCH_OK, rag_result=_rag_result("Vide [S9].", sources))
    _install_fake(monkeypatch, result)

    resp = client.post("/api/ask", json={"query": "q"})

    assert resp.status_code == 200  # AI_SEARCH_OK is still HTTP 200 — the
    # integrity problem is reported in the body, not as an HTTP error.
    body = resp.json()
    assert body["citation_integrity"]["status"] == "INVALID"
    assert body["citation_integrity"]["unknown_labels"] == ["S9"]


# ---------------------------------------------------------------------------
# 4. AI_SEARCH_OK with NO_CITATIONS
# ---------------------------------------------------------------------------


def test_ai_search_ok_with_no_citations(client, monkeypatch):
    sources = [_source("S1")]
    result = _harness_result(HarnessStatus.AI_SEARCH_OK, rag_result=_rag_result("Sem citação nenhuma.", sources))
    _install_fake(monkeypatch, result)

    resp = client.post("/api/ask", json={"query": "q"})

    body = resp.json()
    assert body["citation_integrity"]["status"] == "NO_CITATIONS"
    assert body["citation_integrity"]["cited_labels"] == []


# ---------------------------------------------------------------------------
# 5. AI_SEARCH_NO_EVIDENCE
# ---------------------------------------------------------------------------


def test_ai_search_no_evidence(client, monkeypatch):
    result = _harness_result(
        HarnessStatus.AI_SEARCH_NO_EVIDENCE,
        rag_result=_rag_result(None, [], evidence_available=False),
    )
    _install_fake(monkeypatch, result)

    resp = client.post("/api/ask", json={"query": "GOLD-999"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "AI_SEARCH_NO_EVIDENCE"
    assert body["answer"] is None
    assert body["sources"] == []
    assert body["citation_integrity"] is None
    assert body["message"] == "No relevant information was found for this question."


# ---------------------------------------------------------------------------
# 6. INVALID_REQUEST / empty query
# ---------------------------------------------------------------------------


def test_invalid_request_empty_query(client, monkeypatch):
    result = _harness_result(HarnessStatus.INVALID_REQUEST, error="query must be a non-empty, non-whitespace string")
    _install_fake(monkeypatch, result)

    resp = client.post("/api/ask", json={"query": ""})

    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "INVALID_REQUEST"
    assert body["answer"] is None
    assert "error" not in body  # raw HarnessResult.error is never a response key


# ---------------------------------------------------------------------------
# 7. RAG_EXECUTION_FAILED
# ---------------------------------------------------------------------------


def test_rag_execution_failed(client, monkeypatch):
    result = _harness_result(HarnessStatus.RAG_EXECUTION_FAILED, error="Connection refused to serving endpoint xyz")
    _install_fake(monkeypatch, result)

    resp = client.post("/api/ask", json={"query": "q"})

    assert resp.status_code == 500
    body = resp.json()
    assert body["status"] == "RAG_EXECUTION_FAILED"
    assert body["message"] == "Something went wrong processing this request."


# ---------------------------------------------------------------------------
# 8. API never exposes raw result.error
# ---------------------------------------------------------------------------


def test_raw_error_string_never_appears_in_response_body(client, monkeypatch):
    secret_looking_error = "Connection refused: token=abc123 host=internal-db.example.com"
    result = _harness_result(HarnessStatus.RAG_EXECUTION_FAILED, error=secret_looking_error)
    _install_fake(monkeypatch, result)

    resp = client.post("/api/ask", json={"query": "q"})

    assert secret_looking_error not in resp.text
    assert "token=abc123" not in resp.text


# ---------------------------------------------------------------------------
# 9. Sources serialize without mutation
# ---------------------------------------------------------------------------


def test_sources_serialize_without_mutating_input(client, monkeypatch):
    sources = [_source("S1"), _source("S2")]
    sources_before = copy.deepcopy(sources)
    result = _harness_result(HarnessStatus.AI_SEARCH_OK, rag_result=_rag_result("[S1]", sources))
    _install_fake(monkeypatch, result)

    client.post("/api/ask", json={"query": "q"})

    assert sources == sources_before


# ---------------------------------------------------------------------------
# 10. Traced runtime boundary is called exactly once per valid request
# ---------------------------------------------------------------------------


def test_traced_ask_called_exactly_once_per_request(client, monkeypatch):
    result = _harness_result(HarnessStatus.AI_SEARCH_OK, rag_result=_rag_result("[S1]", [_source("S1")]))
    fake = _install_fake(monkeypatch, result)

    client.post("/api/ask", json={"query": "uma pergunta qualquer"})

    assert len(fake.calls) == 1
    assert fake.calls[0][1] == "uma pergunta qualquer"


def test_traced_ask_called_once_even_for_no_evidence(client, monkeypatch):
    result = _harness_result(
        HarnessStatus.AI_SEARCH_NO_EVIDENCE, rag_result=_rag_result(None, [], evidence_available=False)
    )
    fake = _install_fake(monkeypatch, result)

    client.post("/api/ask", json={"query": "GOLD-999"})

    assert len(fake.calls) == 1
