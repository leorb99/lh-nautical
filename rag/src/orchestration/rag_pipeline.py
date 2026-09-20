"""RAG pipeline: coordinates one RAG request — retrieval-policy execution,
AI Search retrieval, evidence translation, context assembly, and
generation. Introduced in Phase 5A as four separate files (``evidence.py``,
``retriever_adapter.py``, ``context.py``, ``orchestrator.py``); merged into
this single module during the Phase 5A.1 lean-core review once real usage
showed none of the four pieces was ever imported independently of the
others (every import was internal to the package, and each piece exists
only to feed the next) — see that review's report for the full
justification. Behavior is unchanged from Phase 5A.

Deliberately NOT a monolithic ``answer_everything(query)``: every
intermediate value — the ``RetrievalPolicyDecision``, the retrieved
evidence, the assembled context — stays inspectable on the returned
:class:`OrchestrationResult`, so a future AI Harness can call
:meth:`RagOrchestrator.generate` and still see/override each step rather
than getting a single opaque string back.

What this pipeline does NOT do (reserved for the future AI Harness /
Phase 6 — see that phase's design, not implemented here):
- decide AI Search vs. structured/Delta/SQL data, or fetch structured
  evidence — the Harness does that (Phase 6B.1). This module contains no
  SQL and no structured-data client, and it never sees or reproduces
  structured values: for a combined question it only writes the
  *documentary explanation* (see ``values_shown_separately``); the values
  themselves are rendered deterministically elsewhere;
- score or gate evidence sufficiency;
- validate citations;
- apply guardrails;
- log to MLflow.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from databricks.sdk import WorkspaceClient
from llama_index.core import PromptTemplate, QueryBundle
from llama_index.core.retrievers import BaseRetriever
from llama_index.core.schema import NodeWithScore, TextNode

from src.generation.llama_client import DatabricksLlamaLLM
from src.retrieval.ai_search import RetrievedChunk, query_index
from src.retrieval.policy import DEFAULT_POLICY_CONFIG, PolicyConfig, RetrievalPolicyDecision, classify_query

__all__ = [
    "RetrievedEvidence",
    "to_retrieved_evidence",
    "evidence_to_node",
    "chunks_to_evidence_and_nodes",
    "build_context_blocks",
    "GROUNDING_PROMPT_TEMPLATE",
    "EXPLANATION_PROMPT_TEMPLATE",
    "PolicyRetriever",
    "OrchestrationResult",
    "RagOrchestrator",
]


# ---------------------------------------------------------------------------
# Evidence — deterministic internal representation + LlamaIndex node
# translation. Kept as a plain record (not just LlamaIndex node metadata)
# so source metadata surviving the pipeline never depends on a LlamaIndex
# internal representation change.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RetrievedEvidence:
    """One retrieved chunk, carrying every field generation/citation/tracing
    may need, plus its deterministic source label (``S1``, ``S2``, ... —
    assigned by retrieval rank order, never by the model)."""

    source_label: str
    chunk_id: str
    text: str
    document_id: str
    document_title: str | None
    canonical_domain: str | None
    section_path: str | None
    section_title: str | None
    rule_ids: tuple[str, ...]
    score: float
    rank: int


def to_retrieved_evidence(chunks: Sequence[RetrievedChunk]) -> list[RetrievedEvidence]:
    """Assign deterministic ``S1..Sn`` labels in retrieval order (the order
    ``chunks`` already arrives in — this function never re-sorts)."""
    return [
        RetrievedEvidence(
            source_label=f"S{i}",
            chunk_id=c.chunk_id,
            text=c.chunk_text,
            document_id=c.document_id,
            document_title=c.document_title,
            canonical_domain=c.canonical_domain,
            section_path=c.section_path,
            section_title=c.section_title,
            rule_ids=c.rule_ids,
            score=c.score,
            rank=c.rank,
        )
        for i, c in enumerate(chunks, start=1)
    ]


def evidence_to_node(evidence: RetrievedEvidence) -> NodeWithScore:
    """Translate one :class:`RetrievedEvidence` into a LlamaIndex
    ``NodeWithScore``, preserving every field as node metadata."""
    node = TextNode(
        id_=evidence.chunk_id,
        text=evidence.text,
        metadata={
            "source_label": evidence.source_label,
            "chunk_id": evidence.chunk_id,
            "document_id": evidence.document_id,
            "document_title": evidence.document_title,
            "canonical_domain": evidence.canonical_domain,
            "section_path": evidence.section_path,
            "section_title": evidence.section_title,
            "rule_ids": list(evidence.rule_ids),
            "rank": evidence.rank,
        },
    )
    return NodeWithScore(node=node, score=evidence.score)


def chunks_to_evidence_and_nodes(
    chunks: Sequence[RetrievedChunk],
) -> tuple[list[RetrievedEvidence], list[NodeWithScore]]:
    """Convenience wrapper: one deterministic pass from raw AI Search
    results to both representations, so callers never build them out of
    sync with each other's ordering."""
    evidence = to_retrieved_evidence(chunks)
    nodes = [evidence_to_node(e) for e in evidence]
    return evidence, nodes


# ---------------------------------------------------------------------------
# Context assembly — deterministic grounding blocks and the prompt
# contract. Only fields already on RetrievedEvidence are ever injected into
# the prompt — no other repository metadata is exposed to the model.
# ---------------------------------------------------------------------------


def _one_block(source_label: str, document_id: str, section: str | None, rule_ids: Sequence[str], content: str) -> str:
    rule_ids_str = ", ".join(rule_ids) if rule_ids else "none"
    section_str = section or "none"
    return (
        f"[{source_label}]\n"
        f"document_id: {document_id}\n"
        f"section: {section_str}\n"
        f"rule_ids: {rule_ids_str}\n"
        f"content:\n{content}"
    )


def build_context_blocks(evidence: Sequence[RetrievedEvidence]) -> str:
    """Deterministic, retrieval-order context blocks — one ``[Sn]`` block
    per evidence item."""
    blocks = [
        _one_block(e.source_label, e.document_id, e.section_path or e.section_title, e.rule_ids, e.text)
        for e in evidence
    ]
    return "\n\n".join(blocks)


# Core grounding rules: answer only from the supplied evidence blocks; never
# invent business rules or numeric values; state plainly when the evidence
# does not support a claim; cite source labels for factual claims.
# Deliberately small — this is not the future AI Harness prompt (guardrails,
# evidence-sufficiency gating, etc. are out of scope here).

# Answer-style rules shared by every template below (Phase 6B.1): they shape
# how the answer reads, never what the evidence says.
_STYLE_RULES = (
    "- Answer in the same language as the question.\n"
    "- Start with the direct answer, in natural language. Do not open with a "
    "preamble such as \"According to the evidence\" and do not repeat the "
    "question.\n"
    "- Be concise unless the question asks for detail.\n"
    "- Do not include code, DAX or SQL unless the question explicitly asks "
    "for it.\n"
)

GROUNDING_PROMPT_TEMPLATE = PromptTemplate(
    "You are answering a question using ONLY the evidence blocks below, "
    "retrieved from this project's canonical knowledge base.\n\n"
    "Rules:\n"
    "- Answer using only the information present in the evidence blocks.\n"
    "- Do not invent business rules, identifiers, or numeric values that are "
    "not explicitly present in the evidence.\n"
    "- Clearly distinguish what the evidence states from what it does not "
    "state.\n"
    "- If the evidence does not support a factual assertion needed to "
    "answer the question, say so plainly instead of guessing.\n"
    "- When you make a factual claim, cite the source label(s) it comes "
    "from, e.g. [S1], at the end of the sentence it supports.\n"
    "- Do not use outside/general knowledge beyond what is in the evidence.\n"
    + _STYLE_RULES
    + "\nEvidence:\n{context_str}\n\n"
    "Question: {query_str}\n"
    "Answer:"
)

# Combined (BOTH) route: the live values are computed and rendered
# deterministically, outside the model, and shown to the user in their own
# block. The model is only asked for the documentary explanation; it never
# sees, restates or ranks the values. Citations, as always, attach only to
# documentary claims.
EXPLANATION_PROMPT_TEMPLATE = PromptTemplate(
    "You are answering the explanatory part of a question using ONLY the "
    "evidence blocks below, retrieved from this project's canonical "
    "knowledge base.\n\n"
    "The question also asks for live data values (for example a ranking with "
    "quantities). Those values are computed separately and shown to the user "
    "in another block; you do not have them.\n\n"
    "Rules:\n"
    "- Write ONLY the explanation of the rule, definition or calculation "
    "method the question asks about. Do not list, restate, estimate or "
    "invent any ranking, product name, rank or quantity.\n"
    "- Answer using only the information present in the evidence blocks.\n"
    "- Do not invent business rules, identifiers, or numeric values that are "
    "not explicitly present in the evidence.\n"
    "- If the evidence does not support what is needed to explain, say so "
    "plainly instead of guessing.\n"
    "- When you make a factual claim, cite the source label(s) it comes "
    "from, e.g. [S1], at the end of the sentence it supports.\n"
    "- Do not use outside/general knowledge beyond what is in the evidence.\n"
    + _STYLE_RULES
    + "\nEvidence:\n{context_str}\n\n"
    "Question: {query_str}\n"
    "Answer:"
)


# ---------------------------------------------------------------------------
# Retriever adapter — the seam where this codebase meets LlamaIndex's
# BaseRetriever abstraction. Does not classify queries, does not pick a
# query_type, does not pick a Top-K, and does not build a filter — all of
# that is decided once, upstream, by src.retrieval.policy.classify_query,
# and handed in as an already-built RetrievalPolicyDecision. PolicyRetriever
# exists only to (a) execute that decision through the existing
# src.retrieval.ai_search.query_index client and (b) translate the raw
# RetrievedChunk results into LlamaIndex nodes — a transport/translation
# shim, never a second retrieval policy.
# ---------------------------------------------------------------------------


@dataclass
class PolicyRetriever(BaseRetriever):
    """Retrieves exactly once, using a pre-computed
    :class:`~src.retrieval.policy.RetrievalPolicyDecision`. After
    :meth:`retrieve` runs, ``last_evidence`` holds the deterministic,
    source-labeled evidence list for the most recent call — this is what
    :class:`RagOrchestrator` uses to build context blocks and the returned
    ``sources`` metadata, so that metadata never depends on reaching back
    into opaque LlamaIndex node internals.
    """

    client: WorkspaceClient
    index_name: str
    decision: RetrievalPolicyDecision
    query_text: str
    # None means "use the real client" — resolved at call time (see
    # _retrieve), not bound to the imported function at class-definition
    # time, so tests can inject a fake without a live AI Search call.
    query_index_fn: Callable[..., list[RetrievedChunk]] | None = None
    last_evidence: list[RetrievedEvidence] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        super().__init__()

    def _retrieve(self, query_bundle: QueryBundle) -> list[NodeWithScore]:
        # The decision (and therefore query_type/top_k/filters_json) was
        # fixed by the caller before this retriever was constructed; the
        # incoming query_bundle's text is not used to re-derive it, so this
        # never becomes a second, LlamaIndex-internal query classifier.
        query_fn = self.query_index_fn or query_index
        chunks = query_fn(
            self.client,
            self.index_name,
            self.query_text,
            num_results=self.decision.top_k,
            query_type=self.decision.query_type,
            filters_json=self.decision.filters_json,
        )
        evidence, nodes = chunks_to_evidence_and_nodes(chunks)
        self.last_evidence = evidence
        return nodes


# ---------------------------------------------------------------------------
# Orchestrator — the only place that wires retrieval policy, AI Search
# retrieval, context assembly, and generation together into one call.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OrchestrationResult:
    """Everything a caller (including a future Harness) needs: the answer,
    deterministic source metadata independent of what the model actually
    cited, the retrieval decision that produced the evidence, and timing."""

    query: str
    answer: str | None
    evidence_available: bool
    sources: list[dict[str, Any]]
    retrieval_policy_decision: RetrievalPolicyDecision
    model: str | None
    retrieval_latency_s: float
    generation_latency_s: float | None
    total_latency_s: float
    usage: dict[str, Any] | None = field(default=None)


def _usage_from_response(response: Any) -> dict[str, Any] | None:
    raw_usage = (response.raw or {}).get("usage") if isinstance(response.raw, dict) else None
    if raw_usage is None:
        return None
    return {
        "prompt_tokens": getattr(raw_usage, "prompt_tokens", None),
        "completion_tokens": getattr(raw_usage, "completion_tokens", None),
        "total_tokens": getattr(raw_usage, "total_tokens", None),
    }


def _evidence_to_source_dict(e: RetrievedEvidence) -> dict[str, Any]:
    return {
        "source_label": e.source_label,
        "chunk_id": e.chunk_id,
        "document_id": e.document_id,
        "document_title": e.document_title,
        "section_path": e.section_path,
        "section_title": e.section_title,
        "rule_ids": list(e.rule_ids),
        "rank": e.rank,
        "score": e.score,
    }


class RagOrchestrator:
    """Ties the existing Retrieval Policy (Phase 4C) and the Databricks
    Llama LLM adapter together. Holds no retrieval or generation logic of
    its own — both are delegated to already-validated components."""

    def __init__(
        self,
        client: WorkspaceClient,
        index_name: str,
        llm: DatabricksLlamaLLM,
        policy_config: PolicyConfig = DEFAULT_POLICY_CONFIG,
    ) -> None:
        self._client = client
        self._index_name = index_name
        self._llm = llm
        self._policy_config = policy_config

    def generate(
        self,
        query: str,
        *,
        retrieval_decision: RetrievalPolicyDecision | None = None,
        values_shown_separately: bool = False,
    ) -> OrchestrationResult:
        """Run one retrieval call (via the existing policy) followed by, at
        most, one generation call.

        ``retrieval_decision``: normally left ``None``, in which case the
        existing, authoritative :func:`~src.retrieval.policy.classify_query`
        decides the route — this orchestrator never invents its own
        classification. A caller (e.g. a future Harness that already
        computed/overrode a decision) may pass one in directly; the
        decision object stays fully inspectable on the result either way.

        ``values_shown_separately``: normally ``False`` (documentary-only,
        exactly the pre-6B.1 behavior). The Harness sets it for the
        documentary half of a combined question whose live values it already
        rendered deterministically: the prompt then asks the model for the
        explanation only. Retrieval, the empty-retrieval guard and the
        returned sources are identical either way — the natural user
        ``query`` is what reaches AI Search, and no structured data is passed
        to this method, so the model cannot rewrite it.
        """
        total_start = time.monotonic()
        decision = retrieval_decision or classify_query(query, self._policy_config)

        retrieval_start = time.monotonic()
        retriever = PolicyRetriever(
            client=self._client,
            index_name=self._index_name,
            decision=decision,
            query_text=query,
        )
        retriever.retrieve(query)
        retrieval_latency_s = time.monotonic() - retrieval_start
        evidence = retriever.last_evidence

        if not evidence:
            # Empty-retrieval guard: no fabricated answer, no model call,
            # e.g. a syntactically valid but nonexistent Rule ID under
            # EXACT_RULE_ID (GOLD-999).
            return OrchestrationResult(
                query=query,
                answer=None,
                evidence_available=False,
                sources=[],
                retrieval_policy_decision=decision,
                model=None,
                retrieval_latency_s=retrieval_latency_s,
                generation_latency_s=None,
                total_latency_s=time.monotonic() - total_start,
                usage=None,
            )

        context_str = build_context_blocks(evidence)
        template = EXPLANATION_PROMPT_TEMPLATE if values_shown_separately else GROUNDING_PROMPT_TEMPLATE
        prompt = template.format(context_str=context_str, query_str=query)

        generation_start = time.monotonic()
        response = self._llm.complete(prompt)
        generation_latency_s = time.monotonic() - generation_start

        usage = _usage_from_response(response)

        return OrchestrationResult(
            query=query,
            answer=response.text,
            evidence_available=bool(evidence),
            sources=[_evidence_to_source_dict(e) for e in evidence],
            retrieval_policy_decision=decision,
            model=self._llm.model_endpoint,
            retrieval_latency_s=retrieval_latency_s,
            generation_latency_s=generation_latency_s,
            total_latency_s=time.monotonic() - total_start,
            usage=usage,
        )
