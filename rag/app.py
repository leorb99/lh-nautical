"""Databricks App backend — Phase 4B.

The App's one and only responsibility: accept an HTTP question, run it
through the already-validated RAG stack via ``traced_ask``, and serialize a
stable JSON contract. It deliberately contains no retrieval, generation, or
routing logic of its own — everything real is delegated:

    request -> validate shape -> traced_ask() -> HarnessResult
            -> check_citation_integrity() -> stable API response

This file is intentionally the App's *entire* backend surface for V1. Final
UI/UX is explicitly out of scope here (see the Phase 4B task) — this is the
backend contract another consumer (a future UI) is built against.

Deployment layout (why imports below work inside the deployed container):
this file, ``app.yaml``, and ``requirements.txt`` all live at the root of
``rag/``, and the Databricks App resource's ``source_code_path`` points at
``rag`` (not ``rag/app``) specifically so that ``rag/src/`` is deployed
alongside this file as a sibling directory — the exact same relative layout
every other script in this repository already assumes when it does
``from src.harness.harness import ...`` from a ``rag/`` working directory
(``rag/tests/*.py``, ``rag/src/evaluation/run_*.py``). No RAG module is
duplicated or copied into this file or a separate app directory.
"""

from __future__ import annotations

import logging
import os

import mlflow
from databricks.sdk import WorkspaceClient
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.evaluation.citations import check_citation_integrity
from src.generation.llama_client import DatabricksLlamaLLM, DEFAULT_MODEL_ENDPOINT
from src.harness.harness import AIHarness, HarnessStatus
from src.harness.tracing import enable_autolog, traced_ask
from src.orchestration.rag_pipeline import RagOrchestrator

logger = logging.getLogger("rag.app")

# ---------------------------------------------------------------------------
# Portable runtime configuration. Every value is read from the process
# environment — populated by the Bundle App resource's `config.env` (see
# resources/rag_app.yml), never a hardcoded personal catalog/user path.
# RAG_CATALOG has no fallback: if it isn't set, the App fails loudly at
# import time rather than silently defaulting to somebody's personal dev
# catalog. RAG_SCHEMA and the model endpoint DO have defaults because they
# are genuinely constant across every target this project has today (see
# the Phase 4B report's "Runtime Configuration" section for the evidence).
# ---------------------------------------------------------------------------

_CATALOG = os.environ["RAG_CATALOG"]
_SCHEMA = os.environ.get("RAG_SCHEMA", "rag")
_INDEX_NAME = os.environ.get("RAG_INDEX_NAME") or f"{_CATALOG}.{_SCHEMA}.document_chunks_index"
_MODEL_ENDPOINT = os.environ.get("RAG_MODEL_ENDPOINT", DEFAULT_MODEL_ENDPOINT)

# ---------------------------------------------------------------------------
# Runtime construction — built exactly once per App process, at import
# time, never reconstructed per request. Authentication is the App's own
# runtime/service-principal identity: WorkspaceClient() with no arguments
# resolves credentials the same way every other RAG component in this
# repository already does — no static token, no credentials file, no
# secret embedded in source.
# ---------------------------------------------------------------------------

_client = WorkspaceClient()
_llm = DatabricksLlamaLLM(client=_client, model_endpoint=_MODEL_ENDPOINT)
_orchestrator = RagOrchestrator(client=_client, index_name=_INDEX_NAME, llm=_llm)
harness = AIHarness(orchestrator=_orchestrator)

# Where traces go — a deployment concern, deliberately not hardcoded here
# (never the Phase 2A/2B throwaway spike path) and not configured inside
# tracing.py itself (see that module's own docstring). Both env vars are
# provided by the Bundle App resource's `config.env` (resources/rag_app.yml)
# in a real deployment; locally (e.g. import-only smoke tests) they are
# simply unset, and MLflow falls back to its own local default rather than
# failing to import or construct the runtime.
_mlflow_tracking_uri = os.environ.get("MLFLOW_TRACKING_URI")
_mlflow_experiment_id = os.environ.get("MLFLOW_EXPERIMENT_ID")
if _mlflow_tracking_uri:
    mlflow.set_tracking_uri(_mlflow_tracking_uri)
if _mlflow_experiment_id:
    mlflow.set_experiment(experiment_id=_mlflow_experiment_id)

# Exactly once per process — never per request. See tracing.py; not
# modified here, not reimplemented here.
# enable_autolog()


# ---------------------------------------------------------------------------
# API contract — frozen for V1. The caller sends only `query`; it has no
# way to see or choose SourceRoute, RetrievalRoute, top_k, or a model.
# ---------------------------------------------------------------------------


class AskRequest(BaseModel):
    query: str


class SourceOut(BaseModel):
    source_label: str
    chunk_id: str
    document_id: str
    document_title: str | None
    section_path: str | None
    section_title: str | None
    rule_ids: list[str]
    rank: int
    score: float


class CitationIntegrityOut(BaseModel):
    #: "VALID" means only "every cited source label exists" — never a
    #: semantic/groundedness judgment. See check_citation_integrity's own
    #: docstring; this API does not repeat or relabel that meaning.
    status: str
    cited_labels: list[str]
    valid_labels: list[str]
    unknown_labels: list[str]


class AskResponse(BaseModel):
    status: str
    answer: str | None
    sources: list[SourceOut]
    citation_integrity: CitationIntegrityOut | None
    message: str | None


app = FastAPI(title="RAG API")


def _citation_integrity_out(answer: str | None, sources: list[dict]) -> CitationIntegrityOut:
    result = check_citation_integrity(answer, sources)
    return CitationIntegrityOut(
        status=result.status.value,
        cited_labels=list(result.cited_labels),
        valid_labels=list(result.valid_labels),
        unknown_labels=list(result.unknown_labels),
    )


@app.post("/api/ask", response_model=AskResponse)
def ask(request: AskRequest) -> JSONResponse:
    # AI_SEARCH is the only route V1 callers can reach — source_route is
    # never accepted from the request body, so the Harness always falls
    # back to its own DEFAULT_SOURCE_ROUTE (AI_SEARCH) internally.
    result = traced_ask(harness, request.query)
    status = result.status

    if status is HarnessStatus.AI_SEARCH_OK:
        rag = result.rag_result
        body = AskResponse(
            status=status.value,
            answer=rag.answer,
            sources=[SourceOut(**source) for source in rag.sources],
            citation_integrity=_citation_integrity_out(rag.answer, rag.sources),
            message=None,
        )
        return JSONResponse(status_code=200, content=body.model_dump())

    if status is HarnessStatus.AI_SEARCH_NO_EVIDENCE:
        body = AskResponse(
            status=status.value,
            answer=None,
            sources=[],
            citation_integrity=None,
            message="No relevant information was found for this question.",
        )
        return JSONResponse(status_code=200, content=body.model_dump())

    if status is HarnessStatus.INVALID_REQUEST:
        body = AskResponse(
            status=status.value,
            answer=None,
            sources=[],
            citation_integrity=None,
            message="Please provide a non-empty question.",
        )
        return JSONResponse(status_code=400, content=body.model_dump())

    if status is HarnessStatus.RAG_EXECUTION_FAILED:
        # result.error is never exposed to the caller — only to server logs.
        logger.error("RAG_EXECUTION_FAILED for query=%r: %s", request.query, result.error)
        body = AskResponse(
            status=status.value,
            answer=None,
            sources=[],
            citation_integrity=None,
            message="Something went wrong processing this request.",
        )
        return JSONResponse(status_code=500, content=body.model_dump())

    # STRUCTURED_DATA_UNAVAILABLE / STRUCTURED_DATA_FETCHED_NO_GENERATION /
    # BOTH_PARTIAL are unreachable in V1 (source_route is never anything
    # but AI_SEARCH here) — handled safely rather than silently presented
    # as success, in case that ever changes.
    logger.error("Unexpected HarnessStatus reached the V1 API: %s", status)
    body = AskResponse(
        status=status.value,
        answer=None,
        sources=[],
        citation_integrity=None,
        message="This request type is not supported yet.",
    )
    return JSONResponse(status_code=500, content=body.model_dump())


# ---------------------------------------------------------------------------
# Frontend — static files served from the same origin as /api/ask. Mounted
# last, after every API route above, so FastAPI/Starlette keeps matching
# /api/ask first; this mount only catches whatever isn't already handled.
# Serving the frontend from this same process means a same-origin
# fetch('/api/ask') from static/script.js, with no CORS to configure and
# no bearer token to manage in the browser — the Databricks Apps platform
# session handles auth for anyone opening this App from inside the workspace.
# ---------------------------------------------------------------------------
app.mount("/", StaticFiles(directory="static", html=True), name="static")


if __name__ == "__main__":
    # Databricks Apps' documented default for a Python app is `python
    # <my-app.py>`; the runtime port is provided via the DATABRICKS_APP_PORT
    # environment variable (confirmed in current official docs) — read
    # directly here rather than relying on any unverified command-line
    # substitution syntax in app.yaml.
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ["DATABRICKS_APP_PORT"]))
