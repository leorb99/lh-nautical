"""Unit tests for rag/src/retrieval/query_routing.py.

Pure Python only — no databricks-sdk import, no network call. Also
mechanically enforces the "no oracle leakage" rule: this module must never
import anything from rag.evaluation (expected_evidence must never reach
query-construction code).
"""

from __future__ import annotations

import inspect

import pytest

from src.retrieval import query_routing
from src.retrieval.query_routing import (
    build_rule_id_filter_json,
    expand_identifier_query,
    extract_rule_ids,
    extract_single_rule_id,
)


# ---------------------------------------------------------------------------
# No-oracle-leakage boundary (Section 23 of the Phase 4B task)
# ---------------------------------------------------------------------------


def test_module_never_imports_evaluate_retrieval():
    """Mechanical guarantee that query-construction code cannot see
    expected_evidence: the module must not import the evaluation package
    (checked via actual import statements, not prose/comments — this
    module's own docstring legitimately discusses the concept)."""
    import_lines = [
        line.strip()
        for line in inspect.getsource(query_routing).splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    assert not any("evaluate_retrieval" in line or "evaluation" in line for line in import_lines)


def test_functions_never_reference_evaluate_retrieval_module():
    """Runtime guarantee, not just a source-text check: none of this
    module's public functions have ``evaluate_retrieval``/``evaluation`` in
    their global namespace (i.e. it was never imported at all)."""
    assert "evaluate_retrieval" not in query_routing.__dict__
    assert not any("evaluation" in name for name in query_routing.__dict__)


# ---------------------------------------------------------------------------
# extract_rule_ids / extract_single_rule_id — GOLD identifier detection
# ---------------------------------------------------------------------------


def test_extract_rule_ids_bare_identifier():
    assert extract_rule_ids("GOLD-032") == ("GOLD-032",)


def test_extract_rule_ids_embedded_in_sentence():
    assert extract_rule_ids("Explique a regra GOLD-008 do pedido.") == ("GOLD-008",)


def test_extract_rule_ids_no_identifier_present():
    assert extract_rule_ids("Como é calculado o estoque de segurança?") == ()


def test_extract_rule_ids_multiple_distinct():
    assert extract_rule_ids("Compare GOLD-003 e GOLD-008.") == ("GOLD-003", "GOLD-008")


def test_extract_rule_ids_deduplicates_repeated_occurrence():
    assert extract_rule_ids("GOLD-032 ... GOLD-032") == ("GOLD-032",)


def test_extract_rule_ids_preserves_first_occurrence_order():
    assert extract_rule_ids("GOLD-010 antes de GOLD-003") == ("GOLD-010", "GOLD-003")


def test_extract_rule_ids_rejects_malformed_ids():
    # Only exactly 3 digits count, matching parser.py's own convention.
    assert extract_rule_ids("GOLD-12 GOLD-1234 GOLDEN-032") == ()


def test_extract_rule_ids_case_sensitive_by_design():
    # The canonical convention is uppercase GOLD-NNN; lowercase is not a
    # recognized identifier and must not be silently normalized here.
    assert extract_rule_ids("gold-032") == ()


def test_extract_single_rule_id_returns_the_one_id():
    assert extract_single_rule_id("GOLD-032") == "GOLD-032"


def test_extract_single_rule_id_none_when_no_id():
    assert extract_single_rule_id("estoque de segurança") is None


def test_extract_single_rule_id_none_when_multiple_ids():
    assert extract_single_rule_id("GOLD-003 e GOLD-008") is None


# ---------------------------------------------------------------------------
# build_rule_id_filter_json — deterministic filter construction
# ---------------------------------------------------------------------------


def test_build_rule_id_filter_json_shape():
    import json

    result = json.loads(build_rule_id_filter_json("GOLD-032"))
    assert result == {"rule_ids": "GOLD-032"}


def test_build_rule_id_filter_json_is_deterministic():
    assert build_rule_id_filter_json("GOLD-032") == build_rule_id_filter_json("GOLD-032")


def test_build_rule_id_filter_json_differs_per_id():
    assert build_rule_id_filter_json("GOLD-032") != build_rule_id_filter_json("GOLD-008")


# ---------------------------------------------------------------------------
# expand_identifier_query — fixed template, no oracle content
# ---------------------------------------------------------------------------


def test_expand_identifier_query_default_template():
    assert expand_identifier_query("GOLD-032", "GOLD-032") == "regra GOLD-032"


def test_expand_identifier_query_custom_template():
    assert (
        expand_identifier_query("GOLD-008", "GOLD-008", template="artigo {rule_id}")
        == "artigo GOLD-008"
    )


def test_expand_identifier_query_rejects_rule_id_not_in_query_text():
    """Guards against accidentally expanding with a Rule ID pulled from
    somewhere other than the user's own query (e.g. a gold label)."""
    with pytest.raises(ValueError):
        expand_identifier_query("GOLD-032", "GOLD-999")


def test_expand_identifier_query_deterministic():
    a = expand_identifier_query("GOLD-032", "GOLD-032")
    b = expand_identifier_query("GOLD-032", "GOLD-032")
    assert a == b
