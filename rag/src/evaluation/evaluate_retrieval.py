"""Pure-Python retrieval evaluation logic for Phase 4A (Retrieval Quality
Baseline) and its Phase 4A.1 evidence-level correction.

Deliberately has **no** dependency on ``databricks-sdk`` or any network
call — mirrors the existing pure-vs-runtime module boundary already used
by the ingestion pipeline (``persistence_validation.py`` is pure Python;
``persistence.py`` is the Spark/Databricks-dependent counterpart).

This module knows nothing about ``src.retrieval.ai_search.RetrievedChunk``
by design — it operates on the minimal ``RankedResult`` shape below.

--------------------------------------------------------------------------
Phase 4A.1 — evidence-level correction (see rag/evaluation/results/)
--------------------------------------------------------------------------

The Phase 4A baseline had a methodological bug: ``hit_at_k`` and
``evidence_coverage_at_k`` treated "the expected *document* appeared in
Top-K" as success, regardless of *which chunk/section* of that document
appeared. Concretely, query ``BR-03`` ("GOLD-032") never retrieved the
chunk documenting GOLD-032 within Top-15, yet an unrelated chunk from the
same document (its generic "Objetivo" section, rank 11) made
``Hit@15 = True`` under the old document-level logic. That is a false
success: **document match is not evidence match**.

The dataset's unit of ground truth is now :class:`ExpectedEvidence` — a
specific (document, discriminant) pair, not a bare document ID. A result
"satisfies" an evidence unit (see :func:`evidence_satisfied`) only when
its ``document_id`` matches *and* the evidence's own discriminant
(``rule_ids_any`` and/or ``section_cues_any``) is also matched — unless
the evidence unit deliberately declares no discriminant at all (the whole
document genuinely is the expected unit; see ``ExpectedEvidence``
docstring). ``hit_at_k``, ``evidence_coverage_at_k``, and MRR's
``first_relevant_rank`` all now require :func:`evidence_satisfied`, not a
bare document match. ``grade_relevance`` no longer derives grade ``>= 1``
automatically from "same document" — see its docstring for the corrected
0/1/2 rule.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "EvaluationDatasetError",
    "ExpectedEvidence",
    "EvalQuery",
    "RankedResult",
    "load_eval_dataset",
    "evidence_satisfied",
    "grade_relevance",
    "hit_at_k",
    "first_relevant_rank",
    "mrr",
    "evidence_coverage_at_k",
    "precision_at_k",
    "dcg_at_k",
    "ndcg_at_k",
    "generic_heading_hit",
]


class EvaluationDatasetError(ValueError):
    """Raised for a structurally invalid evaluation dataset."""


_REQUIRED_FIELDS = (
    "query_id",
    "query",
    "category",
    "kind",
    "expected_evidence",
    "multi_evidence_required",
)

_REQUIRED_EVIDENCE_FIELDS = ("evidence_id", "document_id", "rule_ids_any", "section_cues_any")

_VALID_CATEGORIES = frozenset(
    {
        "business_rules",
        "inventory",
        "forecasting",
        "metrics",
        "data_architecture",
        "powerbi",
        "cross_source",
        "negative",
    }
)


@dataclass(frozen=True)
class ExpectedEvidence:
    """One unit of gold-standard expected evidence for a query.

    A result satisfies this evidence unit (see :func:`evidence_satisfied`)
    only when its ``document_id`` matches *and* at least one discriminant
    below is also matched:

    - ``rule_ids_any``: satisfied if the result carries any of these Rule
      IDs (exact match against the result's own ``rule_ids`` metadata —
      never a substring search over free text).
    - ``section_cues_any``: satisfied if any cue normalizes-and-matches
      (see :func:`_normalize`) against the result's ``section_path`` /
      ``section_title``.

    If **both** are empty, the evidence unit is a deliberate "whole
    document is the expected unit" case (priority 4 in the Phase 4A.1
    contract) — document match alone satisfies it. This is reserved for
    genuinely document-level questions with no single passage that alone
    answers them; it must not become a fallback used to avoid doing the
    work of picking a real discriminant.
    """

    evidence_id: str
    document_id: str
    rule_ids_any: tuple[str, ...]
    section_cues_any: tuple[str, ...]


@dataclass(frozen=True)
class EvalQuery:
    """One labeled evaluation query. Labels are gold judgments derived from
    reading the canonical corpus — never from AI Search output.

    ``expected_evidence == ()`` marks a negative/OOD query (no valid
    evidence exists in the corpus for it)."""

    query_id: str
    query: str
    category: str
    kind: str
    expected_evidence: tuple[ExpectedEvidence, ...]
    multi_evidence_required: bool
    notes: str = ""

    @property
    def expected_document_ids(self) -> tuple[str, ...]:
        """Derived/auxiliary metadata: the distinct documents referenced by
        this query's expected evidence, in evidence order. Not used by any
        metric — informational only (e.g. for reporting)."""
        seen: list[str] = []
        for e in self.expected_evidence:
            if e.document_id not in seen:
                seen.append(e.document_id)
        return tuple(seen)


@dataclass(frozen=True)
class RankedResult:
    """Minimal shape this module needs from a retrieval result. Any
    retrieval client's richer result type can be adapted into this."""

    rank: int
    document_id: str
    section_path: str | None
    section_title: str | None
    rule_ids: tuple[str, ...]
    score: float


# ---------------------------------------------------------------------------
# Dataset loading
# ---------------------------------------------------------------------------


def _parse_evidence_item(raw: object, line_number: int, query_id: str, index: int) -> ExpectedEvidence:
    if not isinstance(raw, dict):
        raise EvaluationDatasetError(
            f"Line {line_number} ('{query_id}'): expected_evidence[{index}] must be an object"
        )
    missing = [f for f in _REQUIRED_EVIDENCE_FIELDS if f not in raw]
    if missing:
        raise EvaluationDatasetError(
            f"Line {line_number} ('{query_id}'): expected_evidence[{index}] missing field(s) {missing}"
        )
    evidence_id = raw["evidence_id"]
    if not isinstance(evidence_id, str) or not evidence_id.strip():
        raise EvaluationDatasetError(
            f"Line {line_number} ('{query_id}'): expected_evidence[{index}].evidence_id "
            "must be a non-empty string"
        )
    document_id = raw["document_id"]
    if not isinstance(document_id, str) or not document_id.strip():
        raise EvaluationDatasetError(
            f"Line {line_number} ('{query_id}'): expected_evidence[{index}].document_id "
            "must be a non-empty string"
        )
    for field_name in ("rule_ids_any", "section_cues_any"):
        if not isinstance(raw[field_name], list):
            raise EvaluationDatasetError(
                f"Line {line_number} ('{query_id}'): expected_evidence[{index}].{field_name} "
                "must be a list"
            )
    return ExpectedEvidence(
        evidence_id=evidence_id,
        document_id=document_id,
        rule_ids_any=tuple(raw["rule_ids_any"]),
        section_cues_any=tuple(raw["section_cues_any"]),
    )


def load_eval_dataset(path: Path) -> list[EvalQuery]:
    """Load and validate the JSONL evaluation dataset (v2 schema:
    ``expected_evidence``, not ``expected_document_ids``).

    Raises :class:`EvaluationDatasetError` for any malformed item,
    duplicate ``query_id``, or duplicate ``evidence_id`` within one
    query's ``expected_evidence`` — never silently skips or de-duplicates.
    Preserves file order (deterministic output ordering); does not sort.
    """
    text = Path(path).read_text(encoding="utf-8")
    queries: list[EvalQuery] = []
    seen_ids: set[str] = set()

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EvaluationDatasetError(f"Line {line_number}: invalid JSON ({exc})") from exc

        if not isinstance(item, dict):
            raise EvaluationDatasetError(
                f"Line {line_number}: expected a JSON object, got {type(item).__name__}"
            )

        missing = [f for f in _REQUIRED_FIELDS if f not in item]
        if missing:
            raise EvaluationDatasetError(f"Line {line_number}: missing required field(s) {missing}")

        query_id = item["query_id"]
        if not isinstance(query_id, str) or not query_id.strip():
            raise EvaluationDatasetError(f"Line {line_number}: 'query_id' must be a non-empty string")
        if query_id in seen_ids:
            raise EvaluationDatasetError(f"Line {line_number}: duplicate query_id '{query_id}'")
        seen_ids.add(query_id)

        if not isinstance(item["query"], str) or not item["query"].strip():
            raise EvaluationDatasetError(
                f"Line {line_number} ('{query_id}'): 'query' must be a non-empty string"
            )

        category = item["category"]
        if category not in _VALID_CATEGORIES:
            raise EvaluationDatasetError(
                f"Line {line_number} ('{query_id}'): unknown category '{category}' "
                f"(expected one of {sorted(_VALID_CATEGORIES)})"
            )

        if not isinstance(item["expected_evidence"], list):
            raise EvaluationDatasetError(
                f"Line {line_number} ('{query_id}'): 'expected_evidence' must be a list"
            )

        evidence_items = [
            _parse_evidence_item(raw, line_number, query_id, i)
            for i, raw in enumerate(item["expected_evidence"])
        ]
        evidence_ids = [e.evidence_id for e in evidence_items]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise EvaluationDatasetError(
                f"Line {line_number} ('{query_id}'): duplicate evidence_id within expected_evidence"
            )

        if not isinstance(item["multi_evidence_required"], bool):
            raise EvaluationDatasetError(
                f"Line {line_number} ('{query_id}'): 'multi_evidence_required' must be a boolean"
            )
        if item["multi_evidence_required"] and len(evidence_items) < 2:
            raise EvaluationDatasetError(
                f"Line {line_number} ('{query_id}'): multi_evidence_required=true "
                "requires at least 2 expected_evidence items"
            )

        queries.append(
            EvalQuery(
                query_id=query_id,
                query=item["query"],
                category=category,
                kind=item["kind"],
                expected_evidence=tuple(evidence_items),
                multi_evidence_required=item["multi_evidence_required"],
                notes=item.get("notes", ""),
            )
        )

    return queries


# ---------------------------------------------------------------------------
# Evidence matching (Phase 4A.1 core correction)
# ---------------------------------------------------------------------------


def _normalize(text: str) -> str:
    """Lowercase + collapse/trim whitespace. The only normalization applied
    to section cues and to section_path/section_title before comparing —
    documented here as the single source of truth for that rule."""
    return re.sub(r"\s+", " ", text).strip().lower()


def evidence_satisfied(evidence: ExpectedEvidence, result: RankedResult) -> bool:
    """True if ``result`` satisfies ``evidence``.

    Requires ``document_id`` to match. Given a document match:

    - if ``rule_ids_any`` is non-empty, satisfied when the result's own
      ``rule_ids`` metadata exactly intersects it (never a substring
      search over chunk text);
    - else if ``section_cues_any`` is non-empty, satisfied when any cue
      (normalized per :func:`_normalize`) is contained in the result's
      normalized ``section_path``/``section_title``;
    - else (both empty — a deliberate whole-document evidence unit),
      satisfied by the document match alone.

    A document match alone, when the evidence *does* declare a
    discriminant, is **not** sufficient — this is the Phase 4A.1 fix.
    """
    if result.document_id != evidence.document_id:
        return False

    if evidence.rule_ids_any:
        if set(result.rule_ids) & set(evidence.rule_ids_any):
            return True

    if evidence.section_cues_any:
        haystack = _normalize(
            " ".join(part for part in (result.section_path, result.section_title) if part)
        )
        if any(_normalize(cue) in haystack for cue in evidence.section_cues_any):
            return True

    if not evidence.rule_ids_any and not evidence.section_cues_any:
        return True

    return False


def _satisfies_any(query: EvalQuery, result: RankedResult) -> bool:
    return any(evidence_satisfied(e, result) for e in query.expected_evidence)


# ---------------------------------------------------------------------------
# Deterministic, corpus-derived relevance grading (0/1/2) — not an LLM judge
# ---------------------------------------------------------------------------


def grade_relevance(query: EvalQuery, result: RankedResult) -> int:
    """Grade one candidate result against a query's gold evidence.

    2 (RELEVANT):   :func:`evidence_satisfied` is True for at least one of
                     ``query.expected_evidence``.
    1 (PARTIAL):     not satisfied, but the result carries a Rule ID that
                     appears in the *union* of every expected evidence
                     unit's ``rule_ids_any`` — a genuine cross-reference
                     to the right underlying rule from a different
                     document/section than expected, which is useful but
                     not the expected passage.
    0 (IRRELEVANT):  neither of the above — including a same-document
                     result whose evidence unit declares a discriminant
                     that this result does not meet (e.g. the right
                     document's unrelated "Objetivo" section for a
                     Rule-ID-specific query). Grade is **never** derived
                     from document match alone — that shortcut produced
                     the Phase 4A false-success bug and is intentionally
                     not reintroduced here.
    """
    if _satisfies_any(query, result):
        return 2

    all_expected_rule_ids = {
        rid for e in query.expected_evidence for rid in e.rule_ids_any
    }
    if all_expected_rule_ids and set(result.rule_ids) & all_expected_rule_ids:
        return 1

    return 0


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def hit_at_k(query: EvalQuery, results: list[RankedResult], k: int) -> bool:
    """True if at least one expected evidence unit is satisfied
    (:func:`evidence_satisfied`) by some result within Top-K.

    A same-document, wrong-section result does **not** count — this is
    the Phase 4A.1 correction. Undefined (raises) for negative/OOD
    queries.
    """
    if not query.expected_evidence:
        raise ValueError(
            f"hit_at_k is not defined for negative/OOD query '{query.query_id}' "
            "(no expected_evidence)"
        )
    top_k = [r for r in results if r.rank <= k]
    return any(_satisfies_any(query, r) for r in top_k)


def first_relevant_rank(query: EvalQuery, results: list[RankedResult]) -> int | None:
    """Rank (1-based) of the first result that satisfies any expected
    evidence unit (not merely graded >= 1). ``None`` if none does."""
    for r in sorted(results, key=lambda x: x.rank):
        if _satisfies_any(query, r):
            return r.rank
    return None


def mrr(pairs: list[tuple[EvalQuery, list[RankedResult]]]) -> float:
    """Mean Reciprocal Rank over a list of (query, results) pairs, using
    the rank of the first result that actually satisfies expected
    evidence (see :func:`first_relevant_rank`) — not the first result
    from an expected document. Skips negative/OOD queries."""
    scored = [(q, results) for q, results in pairs if q.expected_evidence]
    if not scored:
        return 0.0
    reciprocal_ranks = []
    for q, results in scored:
        rank = first_relevant_rank(q, results)
        reciprocal_ranks.append(1.0 / rank if rank else 0.0)
    return sum(reciprocal_ranks) / len(reciprocal_ranks)


def evidence_coverage_at_k(query: EvalQuery, results: list[RankedResult], k: int) -> float:
    """Fraction of *distinct expected evidence units* satisfied at least
    once within Top-K — not the fraction of expected *documents* present.

    Multiple results satisfying the same evidence unit still count that
    unit once (deduplicated by ``evidence_id``); an irrelevant chunk from
    an expected document does not contribute. Raises for negative/OOD
    queries.
    """
    if not query.expected_evidence:
        raise ValueError(
            f"evidence_coverage_at_k is not defined for negative/OOD query "
            f"'{query.query_id}' (no expected_evidence)"
        )
    top_k = [r for r in results if r.rank <= k]
    satisfied_ids = {
        e.evidence_id for e in query.expected_evidence if any(evidence_satisfied(e, r) for r in top_k)
    }
    return len(satisfied_ids) / len(query.expected_evidence)


def precision_at_k(query: EvalQuery, results: list[RankedResult], k: int) -> float:
    """Precision@K using graded relevance: a candidate counts as relevant
    when ``grade_relevance(query, result) >= 1`` — i.e. either a direct
    evidence match (2) or a genuine cross-referenced Rule ID (1). This is
    deliberately looser than Hit@K/Coverage@K/MRR (which require the
    stricter grade-2-equivalent :func:`evidence_satisfied`): Precision is
    measuring broad candidate-set quality, including partially useful
    cross-references, not "was the exact expected passage found"."""
    top_k = [r for r in results if r.rank <= k]
    if not top_k:
        return 0.0
    relevant = sum(1 for r in top_k if grade_relevance(query, r) >= 1)
    return relevant / len(top_k)


def dcg_at_k(grades: list[int], k: int) -> float:
    """Discounted Cumulative Gain over the first k grades, in rank order."""
    total = 0.0
    for i, grade in enumerate(grades[:k], start=1):
        # i == 1 -> log2(2) == 1, avoids a log2(1) == 0 division.
        total += grade / math.log2(i + 1)
    return total


def ndcg_at_k(grades: list[int], k: int) -> float:
    """Normalized DCG@K. Returns 0.0 when the ideal DCG is 0 (no relevant
    grades at all among ``grades``), rather than dividing by zero."""
    actual = dcg_at_k(grades, k)
    ideal = dcg_at_k(sorted(grades, reverse=True), k)
    if ideal == 0.0:
        return 0.0
    return actual / ideal


# ---------------------------------------------------------------------------
# Generic-heading observation
# ---------------------------------------------------------------------------

#: Headings observed by direct inspection of the 6 canonical documents that
#: are broad/introductory rather than answering a specific question. Not
#: exhaustive of every "Objetivo"-shaped heading in principle — this is the
#: real, closed list found in the corpus at the time of this baseline.
GENERIC_HEADINGS: frozenset[str] = frozenset(
    {
        "objetivo",
        "objetivo e escopo",
        "convenções",
        "validações de qualidade",
        "relações com outros documentos canônicos",
        "relação com os demais documentos canônicos",
        "relação com o corpus canônico existente",
        "proveniência e escopo dos artefatos",
    }
)


def generic_heading_hit(result: RankedResult) -> bool:
    """True if this result's most specific heading (section_title, falling
    back to the last segment of section_path) is one of GENERIC_HEADINGS."""
    heading = result.section_title
    if not heading and result.section_path:
        heading = result.section_path.split(">")[-1].strip()
    if not heading:
        return False
    return heading.strip().lower() in GENERIC_HEADINGS
