"""AI Harness — Phase 1 minimal core.

A thin application-level control layer above the existing RAG runtime
(``src.orchestration.rag_pipeline.RagOrchestrator``) and a future
Structured Data provider. Based on the "AI Harness — Phase 0: Architecture
Preflight" review; see that report for the full design rationale this
module intentionally does not repeat.

Scope boundary, restated because it is easy to blur in a control layer:

- **Retrieval Policy** (``src.retrieval.policy``) decides *how* AI Search
  retrieves evidence once AI Search has been chosen as (part of) the
  source — query classification, Top-K, filtering. This module never
  reimplements, duplicates, or bypasses that.
- **AI Harness** (this module) decides *which* source family a request
  needs (:class:`SourceRoute`) and what controls wrap execution
  (availability handling, controlled degradation, failure containment).
  It never talks to AI Search or the Llama serving endpoint directly —
  every AI Search / generation call goes through
  :meth:`~src.orchestration.rag_pipeline.RagOrchestrator.generate`
  unchanged.

What this module deliberately does NOT do (Phase 1):
- infer :class:`SourceRoute` from query text — no semantic/keyword router
  exists yet; the real Structured Data contract doesn't exist yet either,
  so any keyword vocabulary written today would be a guess, not
  evidence-derived routing (see ``src.retrieval.policy`` for what
  evidence-derived routing actually looks like once it *can* be built);
- implement a real ``StructuredDataProvider`` — no Delta table, catalog,
  schema, or SQL is referenced anywhere in this module;
- merge AI Search evidence and structured evidence into one generation
  call — that needs a real structured-evidence shape to design against;
- validate citations, apply guardrails, or do MLflow tracing — all
  explicitly future work per the Phase 0 review.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, Sequence

from src.orchestration.rag_pipeline import OrchestrationResult, RagOrchestrator

__all__ = [
    "SourceRoute",
    "DEFAULT_SOURCE_ROUTE",
    "StructuredEvidence",
    "StructuredDataProvider",
    "HarnessStatus",
    "HarnessResult",
    "AIHarness",
]


# ---------------------------------------------------------------------------
# Source routing — WHICH source family a request needs. Not to be confused
# with src.retrieval.policy.RetrievalRoute, which decides HOW AI Search
# retrieves once AI_SEARCH has already been chosen as (part of) the source.
# ---------------------------------------------------------------------------


class SourceRoute(Enum):
    AI_SEARCH = "AI_SEARCH"
    STRUCTURED_DATA = "STRUCTURED_DATA"
    BOTH = "BOTH"


#: Phase 1 has no semantic/keyword router (see module docstring) — every
#: call defaults to AI_SEARCH unless a caller (a test, or a future router
#: once one exists) explicitly supplies a route. This is intentional, not
#: a placeholder for a missing default value.
DEFAULT_SOURCE_ROUTE = SourceRoute.AI_SEARCH


# ---------------------------------------------------------------------------
# Structured Data boundary — an interface only. No concrete provider is
# implemented here; see the module docstring for why. Field names are
# deliberately generic: no table/catalog/schema/SQL/environment detail.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StructuredEvidence:
    """One unit of non-document evidence a future structured-data provider
    could contribute. Deliberately generic — ``source_id`` and
    ``description`` are free-form labels, not a schema for any specific
    table or prediction contract, which does not exist yet."""

    source_id: str
    description: str
    content: str


class StructuredDataProvider(Protocol):
    """Contract a future structured-data source (Delta/SQL, ML outputs,
    etc.) would implement. No implementation exists in this repository;
    deciding *whether* and *which* provider to call for a given query is
    this module's job (:class:`AIHarness`), not the provider's own."""

    def is_available(self) -> bool:
        """Whether this provider can currently serve requests. Checked
        before every :meth:`fetch` call — the Harness never calls
        ``fetch`` on a provider that reports itself unavailable."""
        ...

    def fetch(self, query: str) -> Sequence[StructuredEvidence]:
        ...


# ---------------------------------------------------------------------------
# Harness result — status is a small closed Enum rather than scattered
# strings, so callers can switch on it deterministically. rag_result
# reuses OrchestrationResult unchanged (RAG source metadata is never
# duplicated here).
# ---------------------------------------------------------------------------


class HarnessStatus(Enum):
    #: AI_SEARCH executed normally and returned evidence + a grounded answer.
    AI_SEARCH_OK = "AI_SEARCH_OK"
    #: AI_SEARCH executed normally but retrieval returned zero evidence —
    #: RagOrchestrator's own empty-retrieval guard fired; no model call
    #: was made. Distinguished from AI_SEARCH_OK per the Phase 1 spec.
    AI_SEARCH_NO_EVIDENCE = "AI_SEARCH_NO_EVIDENCE"
    #: STRUCTURED_DATA was requested but no provider is configured, or the
    #: configured provider reports itself unavailable. RAG was never
    #: called as a substitute.
    STRUCTURED_DATA_UNAVAILABLE = "STRUCTURED_DATA_UNAVAILABLE"
    #: STRUCTURED_DATA was requested, a provider is available, and
    #: evidence was fetched — but no generation call was made over it.
    #: The current architecture has no non-speculative way to turn raw
    #: structured evidence into a grounded answer yet (see module
    #: docstring); this status makes that boundary explicit rather than
    #: silently returning an incomplete "answer".
    STRUCTURED_DATA_FETCHED_NO_GENERATION = "STRUCTURED_DATA_FETCHED_NO_GENERATION"
    #: BOTH was requested. The AI_SEARCH portion ran (see rag_result);
    #: structured evidence was included only if a provider was available
    #: (see structured_data_available/structured_evidence). This status
    #: is used for every BOTH outcome in Phase 1, whether or not
    #: structured data was available — there is no "complete BOTH"
    #: status yet, because the two are never merged into one generated
    #: answer (see module docstring).
    BOTH_PARTIAL = "BOTH_PARTIAL"
    #: query failed Phase 1.1's minimal request validation (not a string,
    #: or contains only whitespace) — no route was executed, no
    #: RagOrchestrator/StructuredDataProvider call was made.
    INVALID_REQUEST = "INVALID_REQUEST"
    #: RagOrchestrator.generate raised while executing the AI_SEARCH
    #: portion (of an AI_SEARCH or BOTH request) — caught at this
    #: boundary and returned as a controlled result instead of an
    #: uncaught exception. Named for the whole call, not just
    #: "generation": RagOrchestrator.generate covers retrieval, AI Search
    #: transport, LlamaIndex orchestration, and generation, and this
    #: status is raised whenever any of those fail, not only generation
    #: itself.
    RAG_EXECUTION_FAILED = "RAG_EXECUTION_FAILED"


@dataclass(frozen=True)
class HarnessResult:
    """Everything a caller needs to know about the outcome of one
    :meth:`AIHarness.ask` call, with completeness always explicit via
    ``status`` rather than requiring the caller to infer it from which
    fields happen to be populated."""

    query: str
    source_route: SourceRoute
    status: HarnessStatus
    #: The unmodified result of RagOrchestrator.generate(), when the
    #: AI_SEARCH portion ran; None otherwise (e.g. STRUCTURED_DATA route
    #: with no AI_SEARCH portion, INVALID_REQUEST, or RAG_EXECUTION_FAILED
    #: before a result existed).
    rag_result: OrchestrationResult | None
    #: Populated only when a structured-data provider was actually
    #: available and fetched; empty otherwise. Never a placeholder/fake
    #: value.
    structured_evidence: Sequence[StructuredEvidence] = field(default_factory=tuple)
    #: Explicit completeness signal — True only when structured data was
    #: actually available and fetched for this call, independent of
    #: whether it ended up mattering for source_route.
    structured_data_available: bool = False
    #: Human-readable, non-sensitive failure/validation detail; set only
    #: when status is INVALID_REQUEST or RAG_EXECUTION_FAILED.
    error: str | None = None


# ---------------------------------------------------------------------------
# Harness — the one thin orchestration-of-orchestration class.
# ---------------------------------------------------------------------------


class AIHarness:
    """Thin control layer above :class:`RagOrchestrator`. Holds no
    retrieval, generation, or structured-data implementation of its own —
    every real operation is delegated to an already-validated component.
    """

    def __init__(
        self,
        orchestrator: RagOrchestrator,
        structured_data_provider: StructuredDataProvider | None = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._structured_data_provider = structured_data_provider

    def ask(self, query: str, *, source_route: SourceRoute | None = None) -> HarnessResult:
        """Answer ``query`` via the requested source route.

        ``source_route``: normally left ``None``, in which case
        :data:`DEFAULT_SOURCE_ROUTE` (``AI_SEARCH``) is used — Phase 1 has
        no semantic router (see module docstring). Tests and future
        callers may pass a route explicitly.

        Validates ``query`` before touching any route: an invalid query
        never reaches ``RagOrchestrator`` or ``StructuredDataProvider``,
        never raises, and is never silently rewritten (e.g. stripped or
        coerced) — it is returned back on the result exactly as given.
        """
        route = source_route or DEFAULT_SOURCE_ROUTE

        if not self._is_valid_query(query):
            return HarnessResult(
                query=query,
                source_route=route,
                status=HarnessStatus.INVALID_REQUEST,
                rag_result=None,
                error="query must be a non-empty, non-whitespace string",
            )

        if route is SourceRoute.AI_SEARCH:
            return self._ask_ai_search(query, route)
        if route is SourceRoute.STRUCTURED_DATA:
            return self._ask_structured_data(query, route)
        return self._ask_both(query, route)

    @staticmethod
    def _is_valid_query(query: object) -> bool:
        """Minimal Phase 1.1 request validation: ``query`` must be a
        string containing at least one non-whitespace character. No
        further rules — this is deliberately not a place to grow complex
        validation logic."""
        return isinstance(query, str) and query.strip() != ""

    def _ask_ai_search(self, query: str, route: SourceRoute) -> HarnessResult:
        try:
            # The only RAG execution path — no AI Search/LLM transport
            # logic, and no retrieval-policy logic, lives in this class.
            rag_result = self._orchestrator.generate(query)
        except Exception as exc:  # noqa: BLE001 — deliberate: this is the
            # one execution boundary this module is responsible for
            # containing (RagOrchestrator.generate can raise from AI
            # Search transport, the Llama serving call, or LlamaIndex
            # internals). Nothing else in this class is wrapped this
            # broadly — see "Failure handling" in the Phase 1 report.
            return HarnessResult(
                query=query,
                source_route=route,
                status=HarnessStatus.RAG_EXECUTION_FAILED,
                rag_result=None,
                error=str(exc),
            )

        status = HarnessStatus.AI_SEARCH_OK if rag_result.evidence_available else HarnessStatus.AI_SEARCH_NO_EVIDENCE
        return HarnessResult(
            query=query,
            source_route=route,
            status=status,
            rag_result=rag_result,
        )

    def _provider_available(self) -> bool:
        return self._structured_data_provider is not None and self._structured_data_provider.is_available()

    def _ask_structured_data(self, query: str, route: SourceRoute) -> HarnessResult:
        if not self._provider_available():
            # Missing provider and "provider present but unavailable" are
            # deliberately the same outcome — see StructuredDataProvider
            # docstring. RAG is never substituted here.
            return HarnessResult(
                query=query,
                source_route=route,
                status=HarnessStatus.STRUCTURED_DATA_UNAVAILABLE,
                rag_result=None,
            )

        evidence = tuple(self._structured_data_provider.fetch(query))  # type: ignore[union-attr]
        return HarnessResult(
            query=query,
            source_route=route,
            status=HarnessStatus.STRUCTURED_DATA_FETCHED_NO_GENERATION,
            rag_result=None,
            structured_evidence=evidence,
            structured_data_available=True,
        )

    def _ask_both(self, query: str, route: SourceRoute) -> HarnessResult:
        ai_search_result = self._ask_ai_search(query, route)
        if ai_search_result.status is HarnessStatus.RAG_EXECUTION_FAILED:
            # Nothing succeeded — surface the failure as-is rather than
            # masking it behind a "partial" status.
            return ai_search_result

        if self._provider_available():
            structured_evidence = tuple(self._structured_data_provider.fetch(query))  # type: ignore[union-attr]
            structured_available = True
        else:
            structured_evidence = ()
            structured_available = False

        return HarnessResult(
            query=query,
            source_route=route,
            status=HarnessStatus.BOTH_PARTIAL,
            rag_result=ai_search_result.rag_result,
            structured_evidence=structured_evidence,
            structured_data_available=structured_available,
        )
