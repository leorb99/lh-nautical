"""MLflow tracing boundary for the AI Harness — Phase 2B.

Deliberately **not** part of ``harness.py``: ``AIHarness`` stays fully
observability-framework agnostic (no ``import mlflow`` anywhere in that
file), because MLflow is a transversal concern, not a Harness
responsibility. This module is the one place that couples the two, and it
does so from the outside — it calls :meth:`AIHarness.ask`, it is never
called by it.

Phase 2A's runtime spike (see that report) established empirically that
``mlflow.llama_index.autolog()`` already captures, automatically, with no
manual span needed:

- ``PolicyRetriever.retrieve``/``_retrieve`` (retrieved chunks, scores,
  full metadata);
- ``DatabricksLlamaLLM.complete`` (prompt, completion, token usage, model
  name, latency, estimated cost).

What it does **not** capture, because they are Harness-level concepts
invisible to LlamaIndex, is exactly what this module adds: one parent span
around :meth:`AIHarness.ask` (so the two autologged spans above finally
nest under one request trace instead of appearing as two disconnected
traces — the specific gap Phase 2A found), plus a small set of
request-level attributes that autolog has no way to know
(:data:`SourceRoute`, :data:`HarnessStatus`, the resolved
``RetrievalRoute``/``top_k``, and an evidence count). Everything autolog
already provides — token usage, latency, model name, prompt, completion,
chunk contents, scores — is deliberately **not** duplicated here.
"""

from __future__ import annotations

import mlflow
from mlflow.entities import SpanType

from src.harness.harness import AIHarness, HarnessResult, SourceRoute

__all__ = ["enable_autolog", "traced_ask"]


def enable_autolog() -> None:
    """Enable MLflow's LlamaIndex autologging exactly once, at runtime
    initialization. Idempotent (safe to call more than once — MLflow's own
    autolog is itself idempotent); callers should still only call this
    once per process, not per request.

    Does not configure ``mlflow.set_tracking_uri``/``mlflow.set_experiment``
    — where traces are sent is a deployment concern (see the Phase 2A/2B
    reports' "Experiment / trace storage" discussion), not something this
    module hardcodes.
    """
    mlflow.llama_index.autolog()


@mlflow.trace(name="AIHarness.ask", span_type=SpanType.CHAIN)
def traced_ask(harness: AIHarness, query: str, *, source_route: SourceRoute | None = None) -> HarnessResult:
    """The one request-level tracing boundary. Calls
    ``harness.ask(query, source_route=source_route)`` exactly once, inside
    one manual span, and returns the unmodified :class:`HarnessResult` —
    this function changes nothing about Harness behavior, it only observes
    it.

    Request-level attributes are attached only when genuinely known —
    never fabricated. ``source_route``/``harness_status`` are always
    known, because ``HarnessResult`` always carries them (even for
    ``INVALID_REQUEST``, since the route is resolved before validation
    runs — see ``harness.py``). ``retrieval_route``/``top_k``/
    ``evidence_count`` are attached only when ``result.rag_result`` is not
    ``None`` — i.e. only when the AI_SEARCH portion actually ran
    (``AI_SEARCH_OK``, ``AI_SEARCH_NO_EVIDENCE``, or ``BOTH_PARTIAL``),
    never for ``INVALID_REQUEST``, ``STRUCTURED_DATA_UNAVAILABLE``,
    ``STRUCTURED_DATA_FETCHED_NO_GENERATION``, or ``RAG_EXECUTION_FAILED``.
    """
    span = mlflow.get_current_active_span()
    if span is not None:
        # Without this, the decorator auto-captures the raw arguments —
        # including a useless `repr()` of the `harness` object itself.
        # Explicit values take precedence over that automatic capture.
        span.set_inputs({"query": query, "source_route": source_route.value if source_route else None})

    result = harness.ask(query, source_route=source_route)

    if span is not None:
        span.set_attribute("source_route", result.source_route.value)
        span.set_attribute("harness_status", result.status.value)

        if result.rag_result is not None:
            decision = result.rag_result.retrieval_policy_decision
            span.set_attribute("retrieval_route", decision.route.value)
            span.set_attribute("top_k", decision.top_k)
            span.set_attribute("evidence_count", len(result.rag_result.sources))

        # Without this, the decorator auto-captures the full returned
        # HarnessResult — including the entire nested OrchestrationResult
        # (answer, sources, usage, latencies) that the PolicyRetriever/
        # DatabricksLlamaLLM autolog spans already carry in full.
        span.set_outputs({"source_route": result.source_route.value, "harness_status": result.status.value})

    return result
