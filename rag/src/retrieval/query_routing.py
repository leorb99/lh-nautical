"""Pure-Python, oracle-free query-routing helpers for Phase 4B identifier
experiments.

Deliberately has **no** dependency on ``databricks-sdk`` and no import of
``rag.evaluation`` — this module must never see ``expected_evidence``. Every
function here derives its output **only** from the raw user query string
(and, for the filter builder, a Rule ID already extracted from that same
string). This is the "no oracle leakage" boundary required by Phase 4B:
retrieval-side routing may react to what the user actually typed, never to
the evaluation dataset's gold labels (see ``rag/tests/test_query_routing.py``
for the tests that police this boundary).
"""

from __future__ import annotations

import json
import re

__all__ = [
    "GOLD_ID_PATTERN",
    "extract_rule_ids",
    "extract_single_rule_id",
    "build_rule_id_filter_json",
    "expand_identifier_query",
]

#: Same pattern already closed/used by rag/src/ingestion/parser.py for Rule
#: ID extraction from canonical Markdown — reused here, not reinvented, so
#: "what counts as a Rule ID" has one definition in the repo.
GOLD_ID_PATTERN = re.compile(r"\bGOLD-\d{3}\b")


def extract_rule_ids(query_text: str) -> tuple[str, ...]:
    """All distinct Rule IDs found in ``query_text``, in first-occurrence
    order. Empty tuple if none. Never inspects anything but the query
    string itself."""
    seen: list[str] = []
    for match in GOLD_ID_PATTERN.finditer(query_text):
        rid = match.group(0)
        if rid not in seen:
            seen.append(rid)
    return tuple(seen)


def extract_single_rule_id(query_text: str) -> str | None:
    """The one Rule ID in ``query_text``, or ``None`` if zero or more than
    one are present. Deliberately conservative: a query containing two
    different Rule IDs is not a clean "identifier-only" query, so routing
    logic built on this should not guess which one the user meant."""
    ids = extract_rule_ids(query_text)
    if len(ids) == 1:
        return ids[0]
    return None


def build_rule_id_filter_json(rule_id: str) -> str:
    """Deterministic ``filters_json`` string for an exact match against the
    ``rule_ids ARRAY<STRING>`` column, given a single Rule ID string
    already extracted from the raw user query (never from evaluation
    labels). Uses the workspace-confirmed filter syntax for this index:
    ``{"<array_column>": "<value>"}`` matches rows where the array
    contains that value (confirmed empirically in Phase 4B preflight —
    see the experiment report; not documented generically here to avoid
    asserting behavior for indexes/workspaces this was not tested on).
    """
    return json.dumps({"rule_ids": rule_id})


def expand_identifier_query(query_text: str, rule_id: str, template: str = "regra {rule_id}") -> str:
    """Deterministic, fixed-template expansion of a bare-identifier query.

    ``template`` must reference only ``{rule_id}`` — the sole piece of
    information carried over is the Rule ID already present in the user's
    own query text. This function has no access to (and must never be
    given) any evaluation label such as an expected section title; giving
    it one would leak the expected answer into the query under test.

    Requires ``rule_id`` to actually appear in ``query_text`` — a safety
    check against accidentally expanding with a Rule ID that did not come
    from the user's own query (e.g. one pulled from a gold label instead).
    """
    if rule_id not in query_text:
        raise ValueError(
            f"rule_id {rule_id!r} does not appear in query_text {query_text!r} — "
            "refusing to expand with a Rule ID not present in the original query."
        )
    return template.format(rule_id=rule_id)
