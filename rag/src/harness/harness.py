"""AI Harness — control layer above the RAG runtime and structured data.

A thin application-level control layer above the existing RAG runtime
(``src.orchestration.rag_pipeline.RagOrchestrator``) and the structured-data
layer (``src.structured``). Based on the "AI Harness — Phase 0: Architecture
Preflight" review; see that report for the full design rationale this
module intentionally does not repeat.

Scope boundary, restated because it is easy to blur in a control layer:

- **Retrieval Policy** (``src.retrieval.policy``) decides *how* AI Search
  retrieves evidence once AI Search has been chosen as (part of) the
  source — query classification (``EXACT_RULE_ID``/``NORMAL``/
  ``CROSS_SOURCE``), Top-K, filtering. This module never reimplements,
  duplicates, or bypasses that.
- **Source routing** (:class:`SourceRoute`, decided by an injected
  :class:`SourceRouter`) is a different, higher layer: it decides *which
  source family* a request needs — ``AI_SEARCH``, ``STRUCTURED_DATA`` or
  ``BOTH``. The two layers are kept separate on purpose.
- **AI Harness** (this module) coordinates the sources and the controls
  that wrap execution (availability handling, controlled degradation,
  failure containment). It never talks to AI Search, a SQL Warehouse, or
  the Llama serving endpoint directly: documentary evidence goes through
  :meth:`~src.orchestration.rag_pipeline.RagOrchestrator.generate`
  unchanged, and live analytical data goes through an injected
  ``structured_query_service`` (an allowlisted, deterministic query
  service — never model-generated SQL).

Behavior when nothing is injected is exactly the original Phase 1 behavior:
no router means every request defaults to ``AI_SEARCH``, and the legacy
``structured_data_provider`` placeholder keeps its original semantics.

Provenance stays separate: documentary evidence carries ``[Sn]`` source
labels (via ``rag_result.sources``); structured evidence lives on
``structured_result`` and is never given a fake ``[Sn]`` citation. Structured
values reach the user only through
:func:`~src.structured.rendering.render_structured_answer` — plain code, not
a model — so a language model can neither rewrite nor truncate them. A model
is used only for the documentary explanation of a ``BOTH`` question.

What this module deliberately does NOT do:
- generate SQL, or accept SQL from a caller;
- validate citations or do MLflow tracing — see
  ``src.evaluation.citations`` and ``src.harness.tracing``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Protocol, Sequence

from src.orchestration.rag_pipeline import OrchestrationResult, RagOrchestrator
from src.structured.models import StructuredIntent, StructuredResult
from src.structured.rendering import render_structured_answer

__all__ = [
    "SourceRoute",
    "DEFAULT_SOURCE_ROUTE",
    "RouteDecision",
    "SourceRouter",
    "StructuredQueryRunner",
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


@dataclass(frozen=True)
class RouteDecision:
    """Outcome of source routing for one query.

    ``unsupported_reason`` is set when the request needs live structured
    values that no implemented intent can produce: the Harness then returns
    a controlled "unsupported" outcome instead of falling back to
    documentation, which would answer a data question with a description of
    how the data is calculated.
    """

    route: SourceRoute
    structured_intent: StructuredIntent | None = None
    structured_params: Mapping[str, Any] = field(default_factory=dict)
    unsupported_reason: str | None = None
    reason: str = ""


class SourceRouter(Protocol):
    def route(self, query: str) -> RouteDecision:
        """Deterministically decide which source family ``query`` needs."""
        ...


class StructuredQueryRunner(Protocol):
    """The slice of ``StructuredQueryService`` the Harness relies on."""

    def execute(self, intent: StructuredIntent, params: Mapping[str, Any] | None = None) -> StructuredResult: ...


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
    #: STRUCTURED_DATA route executed; ``answer`` is the deterministic
    #: rendering of ``structured_result`` — no model is involved.
    STRUCTURED_DATA_OK = "STRUCTURED_DATA_OK"
    #: BOTH route, complete: ``answer`` is the deterministic rendering of
    #: ``structured_result`` followed by the model-written documentary
    #: explanation (``rag_result``, whose sources carry the ``[Sn]`` labels).
    BOTH_OK = "BOTH_OK"
    #: BOTH route where the structured values were obtained and rendered but
    #: the documentary explanation is missing — either retrieval found no
    #: evidence or the AI Search/generation call failed technically
    #: (``error`` says which). ``answer`` is the structured rendering alone;
    #: valid live data is never discarded because an explanation is missing.
    BOTH_DEGRADED = "BOTH_DEGRADED"
    #: The structured query ran successfully but returned no rows. No
    #: documentation-only answer is substituted for missing values.
    STRUCTURED_DATA_NO_DATA = "STRUCTURED_DATA_NO_DATA"
    #: The structured query failed — invalid parameters, executor/warehouse
    #: failure, or malformed rows. No documentation-only answer is
    #: substituted for missing values.
    STRUCTURED_DATA_FAILED = "STRUCTURED_DATA_FAILED"
    #: The question needs live values that no implemented structured intent
    #: can produce. Nothing was executed and RAG was not used as a substitute.
    STRUCTURED_DATA_UNSUPPORTED = "STRUCTURED_DATA_UNSUPPORTED"


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
    #: Live structured evidence, when the structured route ran successfully.
    #: Kept separate from ``rag_result.sources`` — never given a ``[Sn]`` label.
    structured_result: StructuredResult | None = None
    #: Final user-facing answer for ``STRUCTURED_DATA_OK``, ``BOTH_OK`` and
    #: ``BOTH_DEGRADED``. For the pure AI_SEARCH route the answer stays on
    #: ``rag_result.answer``.
    answer: str | None = None


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
        *,
        structured_query_service: StructuredQueryRunner | None = None,
        source_router: SourceRouter | None = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._structured_data_provider = structured_data_provider
        self._structured_query_service = structured_query_service
        self._source_router = source_router

    def ask(self, query: str, *, source_route: SourceRoute | None = None) -> HarnessResult:
        """Answer ``query`` via the requested source route.

        ``source_route``: normally left ``None``. With an injected
        :class:`SourceRouter` the route is decided from the query text;
        without one, :data:`DEFAULT_SOURCE_ROUTE` (``AI_SEARCH``) is used —
        the original Phase 1 behavior. Tests and callers may still pass a
        route explicitly.

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

        decision = self._source_router.route(query) if self._source_router is not None else None
        if source_route is None and decision is not None:
            route = decision.route

        if route is SourceRoute.AI_SEARCH:
            return self._ask_ai_search(query, route)

        # Structured routes (STRUCTURED_DATA / BOTH).
        if self._structured_query_service is not None:
            return self._ask_with_structured_service(query, route, decision)
        if decision is not None and decision.unsupported_reason is not None:
            return self._unsupported_result(query, route, decision)
        if source_route is None and self._structured_data_provider is None:
            # Auto-routed to a structured route, but nothing is configured to
            # run it: fail closed rather than answering from documentation.
            return HarnessResult(
                query=query,
                source_route=route,
                status=HarnessStatus.STRUCTURED_DATA_UNAVAILABLE,
                rag_result=None,
            )
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

    @staticmethod
    def _unsupported_result(query: str, route: SourceRoute, decision: RouteDecision | None) -> HarnessResult:
        reason = decision.unsupported_reason if decision is not None else None
        return HarnessResult(
            query=query,
            source_route=route,
            status=HarnessStatus.STRUCTURED_DATA_UNSUPPORTED,
            rag_result=None,
            error=reason or "no supported structured intent was resolved for this request",
        )

    def _ask_with_structured_service(
        self, query: str, route: SourceRoute, decision: RouteDecision | None
    ) -> HarnessResult:
        """STRUCTURED_DATA and BOTH, backed by the allowlisted structured
        query service. The values are computed and rendered first,
        deterministically; if they cannot be produced, the request fails
        closed — documentation alone is never substituted for the requested
        values. No model ever reproduces them."""
        intent = decision.structured_intent if decision is not None else None
        if intent is None:
            return self._unsupported_result(query, route, decision)

        try:
            structured = self._structured_query_service.execute(intent, decision.structured_params)  # type: ignore[union-attr]
            structured_answer = render_structured_answer(structured) if structured.row_count else ""
        except Exception as exc:  # noqa: BLE001 — the structured execution
            # boundary: every failure (invalid parameters, executor,
            # malformed rows, rendering) becomes one controlled outcome.
            return HarnessResult(
                query=query,
                source_route=route,
                status=HarnessStatus.STRUCTURED_DATA_FAILED,
                rag_result=None,
                error=str(exc),
            )

        if structured.row_count == 0:
            return HarnessResult(
                query=query,
                source_route=route,
                status=HarnessStatus.STRUCTURED_DATA_NO_DATA,
                rag_result=None,
                structured_result=structured,
                structured_data_available=True,
            )

        if route is SourceRoute.STRUCTURED_DATA:
            return HarnessResult(
                query=query,
                source_route=route,
                status=HarnessStatus.STRUCTURED_DATA_OK,
                rag_result=None,
                structured_result=structured,
                structured_data_available=True,
                answer=structured_answer,
            )

        # BOTH: the values are already final (rendered above, outside any
        # model). The model is only asked for the documentary explanation,
        # and the natural-language query is what reaches retrieval. A missing
        # or failed explanation degrades the answer; it never discards data.
        try:
            rag_result = self._orchestrator.generate(query, values_shown_separately=True)
        except Exception as exc:  # noqa: BLE001 — same boundary as AI_SEARCH
            return self._both_degraded(
                query, route, structured, structured_answer, f"documentary explanation failed: {exc}"
            )
        if not rag_result.evidence_available or not rag_result.answer:
            return self._both_degraded(
                query,
                route,
                structured,
                structured_answer,
                "no documentary evidence was retrieved for the explanation",
                rag_result=rag_result,
            )
        return HarnessResult(
            query=query,
            source_route=route,
            status=HarnessStatus.BOTH_OK,
            rag_result=rag_result,
            structured_result=structured,
            structured_data_available=True,
            answer=f"{structured_answer}\n\n{rag_result.answer}",
        )

    @staticmethod
    def _both_degraded(
        query: str,
        route: SourceRoute,
        structured: StructuredResult,
        structured_answer: str,
        reason: str,
        rag_result: OrchestrationResult | None = None,
    ) -> HarnessResult:
        return HarnessResult(
            query=query,
            source_route=route,
            status=HarnessStatus.BOTH_DEGRADED,
            rag_result=rag_result,
            error=reason,
            structured_result=structured,
            structured_data_available=True,
            answer=structured_answer,
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
