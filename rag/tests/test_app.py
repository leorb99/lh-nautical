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


# ---------------------------------------------------------------------------
# Phase 6B.1 — source routing is internal; the public contract is unchanged.
# Internal statuses from the new STRUCTURED_DATA / BOTH routes are mapped onto
# the EXISTING public statuses, so the frontend needs no changes.
# ---------------------------------------------------------------------------

from src.structured.models import StructuredIntent, StructuredResult, TopProductRow  # noqa: E402

PUBLIC_RESPONSE_KEYS = {"status", "answer", "sources", "citation_integrity", "message"}
PUBLIC_STATUSES = {"AI_SEARCH_OK", "AI_SEARCH_NO_EVIDENCE", "INVALID_REQUEST", "RAG_EXECUTION_FAILED"}


def _structured_result():
    rows = (TopProductRow(1, 456, "Colete Salva-Vidas 2374", 1737), TopProductRow(2, 77, "Bateria Náutica 1945", 1731))
    return StructuredResult(
        intent=StructuredIntent.TOP_PRODUCTS_BY_QUANTITY,
        rows=rows,
        row_count=len(rows),
        source_tables=("gold.fct_sales", "gold.dim_products"),
        catalog="test_catalog",
        applied_filters={"top_n": 10},
    )


def _structured_ok_result():
    return HarnessResult(
        query="q",
        source_route=SourceRoute.STRUCTURED_DATA,
        status=HarnessStatus.STRUCTURED_DATA_OK,
        rag_result=None,
        structured_result=_structured_result(),
        structured_data_available=True,
        answer="Os produtos mais vendidos são:\n1. Colete Salva-Vidas 2374 — 1.737 unidades",
    )


def test_structured_data_success_uses_the_existing_ok_status_with_no_sources(client, monkeypatch):
    _install_fake(monkeypatch, _structured_ok_result())

    resp = client.post("/api/ask", json={"query": "Quais são os produtos mais vendidos?"})

    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == PUBLIC_RESPONSE_KEYS
    assert body["status"] == "AI_SEARCH_OK"
    assert body["answer"].startswith("Os produtos mais vendidos são")
    assert body["sources"] == []  # a SQL result is never presented as a documentary source
    assert body["citation_integrity"]["status"] == "NO_CITATIONS"
    assert body["message"] is None


STRUCTURED_BLOCK = (
    "Os produtos mais vendidos, por quantidade de unidades, são:\n\n"
    "1. Colete Salva-Vidas 2374 — 1.737 unidades\n2. Bateria Náutica 1945 — 1.731 unidades\n\n"
    "Fonte: dados de vendas efetivas."
)


def test_both_success_returns_the_structured_block_plus_the_documentary_explanation(client, monkeypatch):
    rag = _rag_result("O ranking é denso e usa vendas efetivas [S1].", [_source("S1")])
    result = HarnessResult(
        query="q",
        source_route=SourceRoute.BOTH,
        status=HarnessStatus.BOTH_OK,
        rag_result=rag,
        structured_result=_structured_result(),
        structured_data_available=True,
        answer=f"{STRUCTURED_BLOCK}\n\n{rag.answer}",
    )
    _install_fake(monkeypatch, result)

    resp = client.post("/api/ask", json={"query": "q"})

    body = resp.json()
    assert resp.status_code == 200 and set(body) == PUBLIC_RESPONSE_KEYS
    assert body["status"] == "AI_SEARCH_OK"
    assert body["answer"] == f"{STRUCTURED_BLOCK}\n\n{rag.answer}"  # the composed answer, not just the model's text
    assert [s["source_label"] for s in body["sources"]] == ["S1"]
    assert body["citation_integrity"]["status"] == "VALID"
    assert "Colete" not in str(body["sources"])


@pytest.mark.parametrize("rag_result", [None, _rag_result(None, [], evidence_available=False)], ids=["documentary-failure", "no-documentary-evidence"])
def test_both_degraded_keeps_the_structured_values_with_no_sources(client, monkeypatch, rag_result):
    result = HarnessResult(
        query="q",
        source_route=SourceRoute.BOTH,
        status=HarnessStatus.BOTH_DEGRADED,
        rag_result=rag_result,
        structured_result=_structured_result(),
        structured_data_available=True,
        error="documentary explanation failed: index unavailable (internal detail)",
        answer=STRUCTURED_BLOCK,
    )
    _install_fake(monkeypatch, result)

    resp = client.post("/api/ask", json={"query": "q"})

    body = resp.json()
    assert resp.status_code == 200 and set(body) == PUBLIC_RESPONSE_KEYS
    assert body["status"] == "AI_SEARCH_OK"
    assert body["answer"] == STRUCTURED_BLOCK
    assert body["sources"] == []
    assert body["citation_integrity"]["status"] == "NO_CITATIONS"
    assert body["message"] is None
    assert "internal detail" not in resp.text  # logged server-side only


def test_structured_no_data_uses_the_existing_no_evidence_response(client, monkeypatch):
    result = HarnessResult(
        query="q", source_route=SourceRoute.STRUCTURED_DATA, status=HarnessStatus.STRUCTURED_DATA_NO_DATA, rag_result=None
    )
    _install_fake(monkeypatch, result)

    resp = client.post("/api/ask", json={"query": "q"})

    assert resp.status_code == 200
    assert resp.json() == {
        "status": "AI_SEARCH_NO_EVIDENCE",
        "answer": None,
        "sources": [],
        "citation_integrity": None,
        "message": "No relevant information was found for this question.",
    }


def test_unsupported_structured_question_is_a_controlled_no_answer_not_a_new_status(client, monkeypatch):
    result = HarnessResult(
        query="q",
        source_route=SourceRoute.STRUCTURED_DATA,
        status=HarnessStatus.STRUCTURED_DATA_UNSUPPORTED,
        rag_result=None,
        error="no structured analytical intent is implemented for this kind of question",
    )
    _install_fake(monkeypatch, result)

    resp = client.post("/api/ask", json={"query": "Qual foi o faturamento do mês?"})

    body = resp.json()
    assert resp.status_code == 200 and set(body) == PUBLIC_RESPONSE_KEYS
    assert body["status"] in PUBLIC_STATUSES
    assert body["answer"] is None and body["sources"] == [] and body["citation_integrity"] is None
    assert body["message"] == "This question asks for data that is not supported yet."
    assert "intent" not in resp.text  # internal reason is logged, never returned


@pytest.mark.parametrize(
    "status",
    [
        HarnessStatus.STRUCTURED_DATA_FAILED,
        HarnessStatus.STRUCTURED_DATA_UNAVAILABLE,
        HarnessStatus.STRUCTURED_DATA_FETCHED_NO_GENERATION,
        HarnessStatus.BOTH_PARTIAL,
        HarnessStatus.RAG_EXECUTION_FAILED,
    ],
)
def test_every_structured_failure_maps_to_the_existing_generic_failure(client, monkeypatch, status):
    secret = "warehouse e792e2cd0d725b8c timed out; token=abc123"
    result = HarnessResult(query="q", source_route=SourceRoute.STRUCTURED_DATA, status=status, rag_result=None, error=secret)
    _install_fake(monkeypatch, result)

    resp = client.post("/api/ask", json={"query": "q"})

    body = resp.json()
    assert resp.status_code == 500 and set(body) == PUBLIC_RESPONSE_KEYS
    assert body["status"] == "RAG_EXECUTION_FAILED"  # never a new/internal status value
    assert body["message"] == "Something went wrong processing this request."
    assert secret not in resp.text and "e792e2cd0d725b8c" not in resp.text


def test_no_internal_status_or_route_ever_leaks_into_a_public_response(client, monkeypatch):
    for status in HarnessStatus:
        result = HarnessResult(
            query="q",
            source_route=SourceRoute.BOTH,
            status=status,
            rag_result=_rag_result("a [S1]", [_source("S1")]),
            structured_result=_structured_result(),
            answer="a",
        )
        _install_fake(monkeypatch, result)

        body = client.post("/api/ask", json={"query": "q"}).json()

        assert body["status"] in PUBLIC_STATUSES, status
        assert "STRUCTURED" not in body["status"] and "BOTH" not in body["status"]


def test_request_contract_is_unchanged_and_ignores_routing_hints(client, monkeypatch):
    fake = _install_fake(monkeypatch, _structured_ok_result())

    resp = client.post("/api/ask", json={"query": "q", "source_route": "AI_SEARCH", "top_k": 99, "model": "x"})

    assert resp.status_code == 200
    assert len(fake.calls) == 1 and fake.calls[0][1] == "q"  # only `query` is ever used
    assert client.post("/api/ask", json={}).status_code == 422  # `query` is still required


def test_published_openapi_schemas_are_unchanged(client):
    schemas = client.get("/openapi.json").json()["components"]["schemas"]

    assert set(schemas["AskRequest"]["properties"]) == {"query"}
    assert set(schemas["AskResponse"]["properties"]) == PUBLIC_RESPONSE_KEYS
    assert set(schemas["SourceOut"]["properties"]) == {
        "source_label", "chunk_id", "document_id", "document_title", "section_path",
        "section_title", "rule_ids", "rank", "score",
    }
    assert set(schemas["CitationIntegrityOut"]["properties"]) == {"status", "cited_labels", "valid_labels", "unknown_labels"}
    assert set(schemas["AskResponse"]["required"]) == PUBLIC_RESPONSE_KEYS


def test_app_wires_the_router_and_enables_structured_data_only_when_a_warehouse_is_configured():
    from src.harness.source_router import RuleBasedSourceRouter

    assert isinstance(app_module.harness._source_router, RuleBasedSourceRouter)
    warehouse_configured = bool((os.environ.get("SQL_WAREHOUSE_ID") or "").strip())
    assert (app_module.harness._structured_query_service is not None) == warehouse_configured
