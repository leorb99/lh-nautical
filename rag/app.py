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
from pydantic import BaseModel

from src.evaluation.citations import check_citation_integrity
from src.generation.llama_client import DatabricksLlamaLLM, DEFAULT_MODEL_ENDPOINT
from src.harness.harness import AIHarness, HarnessStatus
from src.harness.source_router import RuleBasedSourceRouter
from src.harness.tracing import enable_autolog, traced_ask
from src.orchestration.rag_pipeline import RagOrchestrator
from src.structured.config import StructuredDataConfig
from src.structured.executor import DatabricksSqlExecutor
from src.structured.service import StructuredQueryService

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

# Structured data (live analytics over the Gold layer) is enabled only when a
# SQL Warehouse is configured. DATA_CATALOG falls back to the same Bundle
# catalog as RAG_CATALOG and DATA_SCHEMA to "gold"; SQL_WAREHOUSE_ID has no
# default. Without it, structured questions get a controlled "unavailable"
# outcome — never a documentation-only answer to a data question.
_structured_config = StructuredDataConfig.from_env(os.environ, default_catalog=_CATALOG)
_structured_service = (
    StructuredQueryService(
        DatabricksSqlExecutor(_client, warehouse_id=_structured_config.warehouse_id),
        catalog=_structured_config.catalog,
        schema=_structured_config.schema,
        warehouse_id=_structured_config.warehouse_id,
    )
    if _structured_config is not None
    else None
)

harness = AIHarness(
    orchestrator=_orchestrator,
    structured_query_service=_structured_service,
    source_router=RuleBasedSourceRouter(),
)

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
enable_autolog()


# ---------------------------------------------------------------------------
# API contract — frozen for V1. The caller sends only `query`; it has no
# way to see or choose SourceRoute, RetrievalRoute, top_k, or a model. Source
# routing (AI_SEARCH / STRUCTURED_DATA / BOTH) is decided internally by the
# Harness and is never exposed: the internal statuses it produces are mapped
# onto the existing public statuses below, so the frontend contract is
# unchanged.
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


def _answer_response(answer: str | None, sources: list[dict]) -> JSONResponse:
    # Public status stays "AI_SEARCH_OK" for every successfully answered
    # request, whichever internal route produced it (documentary,
    # structured, or both) — the frontend contract is unchanged. `sources`
    # only ever carries documentary evidence ([Sn] citations); structured
    # values are never given a fake source entry.
    body = AskResponse(
        status=HarnessStatus.AI_SEARCH_OK.value,
        answer=answer,
        sources=[SourceOut(**source) for source in sources],
        citation_integrity=_citation_integrity_out(answer, sources),
        message=None,
    )
    return JSONResponse(status_code=200, content=body.model_dump())


def _no_answer_response(status_code: int, status: str, message: str) -> JSONResponse:
    body = AskResponse(status=status, answer=None, sources=[], citation_integrity=None, message=message)
    return JSONResponse(status_code=status_code, content=body.model_dump())


@app.post("/api/ask", response_model=AskResponse)
def ask(request: AskRequest) -> JSONResponse:
    # The route (AI_SEARCH / STRUCTURED_DATA / BOTH) is decided by the
    # Harness's source router from the query text; it is never accepted from
    # the request body.
    result = traced_ask(harness, request.query)
    status = result.status

    if status is HarnessStatus.AI_SEARCH_OK:
        rag = result.rag_result
        return _answer_response(rag.answer, rag.sources)

    if status is HarnessStatus.BOTH_OK:
        # Deterministic structured block + documentary explanation; only the
        # documentary evidence is a source.
        return _answer_response(result.answer, result.rag_result.sources)

    if status is HarnessStatus.STRUCTURED_DATA_OK:
        return _answer_response(result.answer, [])

    if status is HarnessStatus.BOTH_DEGRADED:
        # The requested values are valid and complete; only the documentary
        # explanation is missing. Return them (no documentary sources)
        # instead of failing the whole request.
        logger.warning("BOTH_DEGRADED for query=%r: %s", request.query, result.error)
        return _answer_response(result.answer, [])

    if status in (HarnessStatus.AI_SEARCH_NO_EVIDENCE, HarnessStatus.STRUCTURED_DATA_NO_DATA):
        return _no_answer_response(
            200, HarnessStatus.AI_SEARCH_NO_EVIDENCE.value, "No relevant information was found for this question."
        )

    if status is HarnessStatus.STRUCTURED_DATA_UNSUPPORTED:
        logger.info("Unsupported structured request for query=%r: %s", request.query, result.error)
        return _no_answer_response(
            200,
            HarnessStatus.AI_SEARCH_NO_EVIDENCE.value,
            "This question asks for data that is not supported yet.",
        )

    if status is HarnessStatus.INVALID_REQUEST:
        return _no_answer_response(400, status.value, "Please provide a non-empty question.")

    # Every remaining status is a failure (RAG_EXECUTION_FAILED,
    # STRUCTURED_DATA_FAILED, STRUCTURED_DATA_UNAVAILABLE, and the legacy
    # placeholder statuses). result.error is never exposed to the caller —
    # only to server logs — and the public status stays the existing one.
    logger.error("%s for query=%r: %s", status.value, request.query, result.error)
    return _no_answer_response(
        500, HarnessStatus.RAG_EXECUTION_FAILED.value, "Something went wrong processing this request."
    )


if __name__ == "__main__":
    # Databricks Apps' documented default for a Python app is `python
    # <my-app.py>`; the runtime port is provided via the DATABRICKS_APP_PORT
    # environment variable (confirmed in current official docs) — read
    # directly here rather than relying on any unverified command-line
    # substitution syntax in app.yaml.
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ["DATABRICKS_APP_PORT"]))
