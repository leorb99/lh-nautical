"""Phase 4C — deterministic retrieval policy.

Pure Python: no ``databricks-sdk`` import, no import of
``rag.evaluation`` (mechanically enforced by
``test_policy.py::test_module_never_imports_evaluate_retrieval``, mirroring
the same guarantee already built for ``query_routing.py`` in Phase 4B).
``expected_evidence`` must never reach this module.

This module only **decides** a route (:func:`classify_query` ->
:class:`RetrievalPolicyDecision`); it never calls AI Search itself. The
caller (a Databricks-dependent driver, e.g. a future
``run_policy_validation.py``) is responsible for taking a
``RetrievalPolicyDecision`` and issuing the actual query via
``src.retrieval.ai_search.query_index``. This separation is deliberate —
see the Phase 4C task's "Separate policy from execution" — so a future AI
Harness can own/call the routing policy without owning the transport.

--------------------------------------------------------------------------
Routes, in precedence order
--------------------------------------------------------------------------

1. ``EXACT_RULE_ID`` — the entire query, once whitespace-trimmed, is a
   single ``GOLD-NNN`` token and nothing else. Evidence (Phase 4B,
   Experiment A2): Hit@5 = 1.000, MRR = 0.631, the strongest measured
   identifier route — HYBRID + a ``rule_ids`` metadata filter built only
   from that same token. A syntactically valid but nonexistent Rule ID
   (e.g. ``GOLD-999``) still takes this route and is expected to return
   zero results — see :data:`EXACT_RULE_ID_TOP_K` and the module docstring
   of ``query_routing.py`` for why no semantic fallback is attempted.

2. ``CROSS_SOURCE`` — the query is not an exact identifier, and its text
   contains an explicit cue for *both* the Gold/dbt side and the Power BI
   side (see :func:`is_explicit_cross_source`). Deliberately conservative:
   a query naming only one side never matches, and no NLP/LLM intent
   inference is performed. False negatives (a genuinely cross-source
   question that doesn't use one of these literal cues) are accepted —
   they fall through to ``NORMAL`` — in preference to inventing
   cross-source intent the query text does not actually state.

3. ``NORMAL`` — everything else. HYBRID, the default single-source Top-K.

None of these routes currently select ``FULL_TEXT`` as the *primary*
query_type — Phase 4B showed the ``rule_ids`` filter route has stronger
MRR (0.631 vs 0.443) for identifiers, and FULL_TEXT was never evaluated
for non-identifier queries at all. ``FULL_TEXT_FALLBACK`` remains defined
here only as a documented, not-yet-wired escape hatch for a future case
where the filter capability itself is technically unavailable — it is not
used by :func:`classify_query` and must not be inferred from "the filter
returned zero results" (that is a valid, expected outcome for a
nonexistent Rule ID, not a technical failure).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from src.retrieval.query_routing import build_rule_id_filter_json

__all__ = [
    "RetrievalRoute",
    "RetrievalPolicyDecision",
    "PolicyConfig",
    "DEFAULT_POLICY_CONFIG",
    "classify_exact_rule_id",
    "is_explicit_cross_source",
    "classify_query",
]

#: Whole-string match only (after stripping surrounding whitespace) — this
#: is what distinguishes EXACT_RULE_ID from a prose query that merely
#: *contains* a Rule ID (e.g. "Explique GOLD-032"), which must NOT take
#: this route (Phase 4C Section 9).
_EXACT_RULE_ID_PATTERN = re.compile(r"^GOLD-\d{3}$", re.IGNORECASE)

#: Gold/dbt-side cue terms. Deliberately small and literal — see module
#: docstring on why false negatives are preferred over guessing. The
#: negative lookahead (`(?!-\d)`) excludes "Gold" when it is actually part
#: of a `GOLD-NNN` Rule ID token (e.g. "Como GOLD-032 aparece no Power
#: BI?") — mentioning a Rule ID is not, by itself, an explicit request for
#: Gold-side documentary evidence as a whole; that query is routed NORMAL,
#: not CROSS_SOURCE, purely from an accidental substring match.
_GOLD_SIDE_CUES: tuple[re.Pattern, ...] = (re.compile(r"\bgold\b(?!-\d)", re.IGNORECASE),)

#: Power BI-side cue terms.
_POWERBI_SIDE_CUES: tuple[re.Pattern, ...] = (
    re.compile(r"\bpower\s*bi\b", re.IGNORECASE),
    re.compile(r"\bdashboard\b", re.IGNORECASE),
    re.compile(r"\bmodelo sem[âa]ntico\b", re.IGNORECASE),
    re.compile(r"\bsemantic model\b", re.IGNORECASE),
)

#: Top-K for the EXACT_RULE_ID route — fixed by Phase 4B evidence (A2:
#: Hit@5 = 1.000), not part of PolicyConfig since it is not one of the
#: values Section 6–8's offline analysis was about.
EXACT_RULE_ID_TOP_K = 5


class RetrievalRoute(Enum):
    EXACT_RULE_ID = "EXACT_RULE_ID"
    CROSS_SOURCE = "CROSS_SOURCE"
    NORMAL = "NORMAL"


@dataclass(frozen=True)
class RetrievalPolicyDecision:
    """The output of :func:`classify_query` — a decision only, never an AI
    Search call. ``filters_json``/``extracted_rule_id`` are derived solely
    from the query text (see module docstring)."""

    route: RetrievalRoute
    query_type: str
    top_k: int
    filters_json: str | None
    extracted_rule_id: str | None
    reason: str


@dataclass(frozen=True)
class PolicyConfig:
    """The two Top-K values Phase 4C's offline analysis (Sections 6–8)
    determines. Kept as an explicit, immutable, injectable config rather
    than hardcoded constants inside :func:`classify_query`, so a future
    re-run of the offline analysis can change them without touching
    routing logic."""

    normal_top_k: int
    cross_source_top_k: int


#: Derived from the Phase 4C offline analysis (0 new AI Search calls —
#: reused Phase 4A.1's existing Top-15 results), reclassifying the 37-query
#: v2 dataset into 29 normal_single_source queries (incl. CS-04, which the
#: conservative cross-source cue rule does not catch — see
#: ``is_explicit_cross_source`` docstring), 3 cross_source queries
#: (CS-01/02/03), 1 exact identifier (BR-03), and 4 negative/OOD kept
#: separate:
#:
#: normal_single_source (29 queries): Hit@8 = Coverage@8 = 1.000 (the one
#: prior miss, FC-05, resolves exactly at K=8 — its evidence sits at
#: rank 6); Precision decays from 0.437 (K=3) to 0.110 (K=15) with no
#: further Hit/Coverage gain past K=8, and nDCG is essentially flat
#: (0.810 at K=3 vs 0.809 at K=8) — so K=8 is the smallest K with zero
#: known misses, not a max-recall pick.
#:
#: cross_source (3 queries): Coverage@8 = 0.833, Coverage@10 = 1.000 (CS-01
#: and CS-03 both need K>5; only K=10 clears every query) — nDCG@10 = 0.890
#: vs nDCG@15 = 0.890 (identical), so K=15 buys nothing over K=10. Sample
#: size is small (3 queries) — see "Known limitations" in the Phase 4C
#: report for that caveat.
DEFAULT_POLICY_CONFIG = PolicyConfig(normal_top_k=8, cross_source_top_k=10)


def classify_exact_rule_id(query_text: str) -> str | None:
    """The single Rule ID if ``query_text`` (whitespace-trimmed) is
    exactly one ``GOLD-NNN`` token, case-insensitive; ``None`` otherwise.
    Returns the token normalized to uppercase (matching the corpus's own
    ``rule_ids`` casing), regardless of the casing typed by the user."""
    normalized = query_text.strip()
    if _EXACT_RULE_ID_PATTERN.match(normalized):
        return normalized.upper()
    return None


def is_explicit_cross_source(query_text: str) -> bool:
    """True only when ``query_text`` contains at least one Gold-side cue
    *and* at least one Power BI-side cue. Never true for a query naming
    only one side — see module docstring."""
    has_gold = any(pattern.search(query_text) for pattern in _GOLD_SIDE_CUES)
    has_powerbi = any(pattern.search(query_text) for pattern in _POWERBI_SIDE_CUES)
    return has_gold and has_powerbi


def classify_query(query_text: str, config: PolicyConfig = DEFAULT_POLICY_CONFIG) -> RetrievalPolicyDecision:
    """Classify ``query_text`` and return the retrieval route to use.

    Precedence: EXACT_RULE_ID, then CROSS_SOURCE, then NORMAL (Phase 4C
    Section 17) — exact-identifier only when the *entire* query is the
    identifier, so a mixed query like "Como GOLD-032 aparece no Power BI?"
    never takes the identifier route (it falls through to CROSS_SOURCE,
    since it happens to name both sides too, or otherwise to NORMAL).
    """
    rule_id = classify_exact_rule_id(query_text)
    if rule_id is not None:
        return RetrievalPolicyDecision(
            route=RetrievalRoute.EXACT_RULE_ID,
            query_type="HYBRID",
            top_k=EXACT_RULE_ID_TOP_K,
            filters_json=build_rule_id_filter_json(rule_id),
            extracted_rule_id=rule_id,
            reason=(
                "Query is exactly one Rule ID. Phase 4B Experiment A2 "
                "(HYBRID + rule_ids filter) measured Hit@5=1.000, MRR=0.631 "
                "on the identifier subset — the strongest identifier route found."
            ),
        )

    if is_explicit_cross_source(query_text):
        return RetrievalPolicyDecision(
            route=RetrievalRoute.CROSS_SOURCE,
            query_type="HYBRID",
            top_k=config.cross_source_top_k,
            filters_json=None,
            extracted_rule_id=None,
            reason=(
                "Query text explicitly names both a Gold-side and a Power BI-side "
                "cue. Routed at cross_source_top_k, derived from the Phase 4A.1 "
                "offline cross-source coverage analysis."
            ),
        )

    return RetrievalPolicyDecision(
        route=RetrievalRoute.NORMAL,
        query_type="HYBRID",
        top_k=config.normal_top_k,
        filters_json=None,
        extracted_rule_id=None,
        reason=(
            "No exact-identifier or explicit cross-source cue detected; "
            "default single-source HYBRID route at normal_top_k."
        ),
    )
