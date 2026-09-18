"""Deterministic citation-integrity validation — Phase 3B.

A reusable evaluation primitive, not Harness orchestration logic — it lives
here (sibling to ``evaluate_retrieval.py``) rather than inside
``src/harness/`` for the same reason that module is pure Python: it is a
quality/evaluation concern that operates on already-produced results, not a
step in producing them. Pure Python, no dependency on Databricks, MLflow,
LlamaIndex, or the Harness — callable equally from a unit test, an offline
evaluation runner, or (later, if ever wired in) a Harness caller.

Scope, deliberately narrow (Phase 3A preflight's own distinction):

- **CITATION INTEGRITY** (this module) — does every ``[Sn]`` label the
  answer cites actually exist in ``OrchestrationResult.sources``? A pure
  structural/string check against data already available — no model call,
  no semantics.
- **CLAIM GROUNDEDNESS** (explicitly *not* this module) — does the cited
  source's content actually support the claim made next to it? That
  requires reading meaning, not structure, and remains offline/judge-based
  future work (MLflow's ``RetrievalGroundedness`` scorer, if/when that
  path is pursued) — seeing this module validate integrity must never be
  read as "the answer is grounded."

Supported citation syntax — only what real model output has actually
demonstrated (see ``rag/evaluation/results/orchestration_v1/smoke_test_results.json``):
a bracketed, comma-separated list of one or more ``S<digits>`` labels,
e.g. ``[S1]`` or ``[S1, S4]``, with reasonable whitespace variance
(``[S1,S4]``, ``[S1,  S4]``). A bare ``S1`` with no surrounding brackets is
never treated as a citation — no other syntax is invented or supported.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Sequence

__all__ = ["CitationIntegrityStatus", "CitationIntegrityResult", "check_citation_integrity"]

#: One bracketed citation group: `[S1]`, `[S1, S4]`, `[S1,S4]`, `[S1,  S4]`.
#: Requires literal brackets — a bare `S1` in running text never matches.
_CITATION_GROUP_PATTERN = re.compile(r"\[\s*S\d+(?:\s*,\s*S\d+)*\s*\]")

#: One label within an already-matched bracket group.
_LABEL_PATTERN = re.compile(r"S\d+")


class CitationIntegrityStatus(Enum):
    """Distinguishes "nothing to validate" from a validated outcome — never
    conflate the two. ``NO_CITATIONS`` is not itself a grounding signal:
    it only means the answer used no ``[Sn]`` markers, not that it is
    (or isn't) supported by the evidence."""

    NO_CITATIONS = "NO_CITATIONS"
    VALID = "VALID"
    INVALID = "INVALID"


@dataclass(frozen=True)
class CitationIntegrityResult:
    """Structural citation-integrity outcome for one answer. Never asserts
    anything about whether a cited source actually supports its claim —
    see the module docstring's CITATION INTEGRITY vs. CLAIM GROUNDEDNESS
    distinction."""

    status: CitationIntegrityStatus
    citations_present: bool
    #: Unique labels, in first-occurrence order across the whole answer —
    #: a repeated `[S1] ... [S1]` contributes `S1` once.
    cited_labels: tuple[str, ...]
    #: Subset of `cited_labels` that exist in `sources`, in the same
    #: first-occurrence order.
    valid_labels: tuple[str, ...]
    #: Subset of `cited_labels` that do NOT exist in `sources`, in the
    #: same first-occurrence order.
    unknown_labels: tuple[str, ...]
    #: True whenever there is no integrity violation to report — this
    #: includes both `NO_CITATIONS` (nothing to invalidate) and `VALID`.
    #: False only for `INVALID`. Check `status`/`citations_present`
    #: directly when the "no citations" case must be handled distinctly.
    is_valid: bool


def _extract_cited_labels(answer: str) -> tuple[str, ...]:
    """Unique `S<digits>` labels found inside bracketed citation groups, in
    first-occurrence order. Never matches a label outside brackets."""
    seen: list[str] = []
    for group in _CITATION_GROUP_PATTERN.findall(answer):
        for label in _LABEL_PATTERN.findall(group):
            if label not in seen:
                seen.append(label)
    return tuple(seen)


def check_citation_integrity(
    answer: str | None,
    sources: Sequence[Mapping[str, object]],
) -> CitationIntegrityResult:
    """Check whether every `[Sn]`/`[Sn, Sm]` citation label in `answer`
    corresponds to an actual `source_label` in `sources` (the same shape
    as `OrchestrationResult.sources`).

    Pure function: never mutates `answer` or `sources`, never calls
    Databricks/MLflow/LlamaIndex/the Harness. `answer=None` or an empty/
    whitespace-only string is treated as having no citations, not as an
    error.
    """
    text = answer or ""
    cited_labels = _extract_cited_labels(text)

    known_labels = {source["source_label"] for source in sources}
    valid_labels = tuple(label for label in cited_labels if label in known_labels)
    unknown_labels = tuple(label for label in cited_labels if label not in known_labels)

    if not cited_labels:
        status = CitationIntegrityStatus.NO_CITATIONS
    elif unknown_labels:
        status = CitationIntegrityStatus.INVALID
    else:
        status = CitationIntegrityStatus.VALID

    return CitationIntegrityResult(
        status=status,
        citations_present=bool(cited_labels),
        cited_labels=cited_labels,
        valid_labels=valid_labels,
        unknown_labels=unknown_labels,
        is_valid=status is not CitationIntegrityStatus.INVALID,
    )
