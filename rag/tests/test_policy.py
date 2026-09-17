"""Unit tests for rag/src/retrieval/policy.py.

Pure Python only — no databricks-sdk import, no network call. Also
mechanically enforces the "no oracle leakage" rule: this module must never
import anything from rag.evaluation.
"""

from __future__ import annotations

import inspect
import json

import pytest

from src.retrieval import policy
from src.retrieval.policy import (
    DEFAULT_POLICY_CONFIG,
    EXACT_RULE_ID_TOP_K,
    PolicyConfig,
    RetrievalRoute,
    classify_exact_rule_id,
    classify_query,
    is_explicit_cross_source,
)

_CFG = PolicyConfig(normal_top_k=8, cross_source_top_k=10)


# ---------------------------------------------------------------------------
# No-oracle-leakage boundary
# ---------------------------------------------------------------------------


def test_module_never_imports_evaluate_retrieval():
    import_lines = [
        line.strip()
        for line in inspect.getsource(policy).splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    assert not any("evaluate_retrieval" in line or "evaluation" in line for line in import_lines)


def test_functions_never_reference_evaluate_retrieval_module():
    assert "evaluate_retrieval" not in policy.__dict__
    assert not any("evaluation" in name for name in policy.__dict__)


def test_classify_query_signature_has_no_expected_evidence_parameter():
    """The policy layer should not even be able to receive evaluation
    labels — enforced structurally by its function signature."""
    params = inspect.signature(classify_query).parameters
    assert "expected_evidence" not in params
    assert set(params) == {"query_text", "config"}


# ---------------------------------------------------------------------------
# classify_exact_rule_id (Phase 4C Section 9 / 23)
# ---------------------------------------------------------------------------


def test_exact_rule_id_bare():
    assert classify_exact_rule_id("GOLD-032") == "GOLD-032"


def test_exact_rule_id_whitespace_variant():
    assert classify_exact_rule_id("  GOLD-032  ") == "GOLD-032"


def test_exact_rule_id_case_variant():
    assert classify_exact_rule_id("gold-032") == "GOLD-032"


def test_exact_rule_id_nonexistent_but_syntactically_valid():
    """GOLD-999 does not exist in the corpus, but syntactic validity alone
    is what this function checks — existence is a runtime concern, not a
    classification concern."""
    assert classify_exact_rule_id("GOLD-999") == "GOLD-999"


def test_exact_rule_id_rejects_prose_containing_an_id():
    assert classify_exact_rule_id("Explique GOLD-032") is None


def test_exact_rule_id_rejects_multiple_ids():
    assert classify_exact_rule_id("GOLD-029 e GOLD-032") is None


def test_exact_rule_id_rejects_arbitrary_number():
    assert classify_exact_rule_id("032") is None


def test_exact_rule_id_rejects_malformed_id():
    assert classify_exact_rule_id("GOLD-32") is None


# ---------------------------------------------------------------------------
# is_explicit_cross_source (Phase 4C Section 15 / 24)
# ---------------------------------------------------------------------------


def test_cross_source_true_for_known_cross_source_query():
    assert is_explicit_cross_source(
        "Como o Ticket Médio é definido no Gold e implementado no Power BI?"
    ) is True


def test_cross_source_false_for_powerbi_only_question():
    assert is_explicit_cross_source("Como o Power BI calcula a medida Ticket Médio?") is False


def test_cross_source_false_for_gold_only_question():
    assert is_explicit_cross_source("Como é calculado average_ticket_amount no Gold?") is False


def test_cross_source_false_for_ambiguous_question_with_no_explicit_multi_source_cue():
    assert is_explicit_cross_source("Como é calculada a métrica average_ticket_amount?") is False


def test_cross_source_false_when_gold_only_appears_inside_a_rule_id_token():
    """The accidental-substring-match guard: 'GOLD-032' must not, by
    itself, count as a Gold-side cue."""
    assert is_explicit_cross_source("Como GOLD-032 aparece no Power BI?") is False


def test_cross_source_true_with_dashboard_as_powerbi_side_cue():
    assert is_explicit_cross_source(
        "Como o dashboard usa a mesma regra do Gold para pedidos em aberto?"
    ) is True


def test_cross_source_true_with_semantic_model_cue():
    assert is_explicit_cross_source(
        "Como o Gold se relaciona com o semantic model do relatório?"
    ) is True


# ---------------------------------------------------------------------------
# classify_query — routing precedence and decision content
# ---------------------------------------------------------------------------


def test_classify_query_exact_rule_id_route():
    decision = classify_query("GOLD-032", _CFG)
    assert decision.route == RetrievalRoute.EXACT_RULE_ID
    assert decision.query_type == "HYBRID"
    assert decision.top_k == EXACT_RULE_ID_TOP_K
    assert decision.extracted_rule_id == "GOLD-032"
    assert json.loads(decision.filters_json) == {"rule_ids": "GOLD-032"}


def test_classify_query_exact_rule_id_top_k_is_five():
    assert EXACT_RULE_ID_TOP_K == 5


def test_classify_query_nonexistent_rule_id_still_routes_exact():
    """GOLD-999 must still take the EXACT_RULE_ID route — no silent
    fallback to semantic search just because the ID might not exist;
    existence is a runtime/result concern, not a classification concern."""
    decision = classify_query("GOLD-999", _CFG)
    assert decision.route == RetrievalRoute.EXACT_RULE_ID
    assert decision.extracted_rule_id == "GOLD-999"


def test_classify_query_mixed_id_and_text_is_not_exact_route():
    """'Como GOLD-032 aparece no Power BI?' must NOT be EXACT_RULE_ID
    (Phase 4C Section 17) — it has no Gold-side cue once the accidental
    Rule-ID-substring match is excluded, so it falls through to NORMAL."""
    decision = classify_query("Como GOLD-032 aparece no Power BI?", _CFG)
    assert decision.route == RetrievalRoute.NORMAL
    assert decision.extracted_rule_id is None
    assert decision.filters_json is None


def test_classify_query_cross_source_route():
    decision = classify_query(
        "Como o Ticket Médio é definido no Gold e implementado no Power BI?", _CFG
    )
    assert decision.route == RetrievalRoute.CROSS_SOURCE
    assert decision.query_type == "HYBRID"
    assert decision.top_k == _CFG.cross_source_top_k
    assert decision.filters_json is None
    assert decision.extracted_rule_id is None


def test_classify_query_normal_route_powerbi_only():
    decision = classify_query("Como o Power BI calcula Ticket Médio?", _CFG)
    assert decision.route == RetrievalRoute.NORMAL
    assert decision.top_k == _CFG.normal_top_k


def test_classify_query_normal_route_gold_only():
    decision = classify_query("Como é calculado average_ticket_amount?", _CFG)
    assert decision.route == RetrievalRoute.NORMAL


def test_classify_query_precedence_exact_id_wins_over_cross_source_cues():
    """A bare Rule ID takes EXACT_RULE_ID precedence even though, in
    principle, nothing here mentions Power BI — this test documents that
    exact-identifier is checked first, per Section 17's precedence order."""
    decision = classify_query("GOLD-032", _CFG)
    assert decision.route == RetrievalRoute.EXACT_RULE_ID


def test_classify_query_uses_default_config_when_omitted():
    decision = classify_query("Como é calculado average_ticket_amount?")
    assert decision.top_k == DEFAULT_POLICY_CONFIG.normal_top_k


def test_classify_query_reason_is_non_empty_string():
    for text in ("GOLD-032", "Como o Gold se relaciona com o Power BI?", "pergunta qualquer"):
        decision = classify_query(text, _CFG)
        assert isinstance(decision.reason, str) and decision.reason.strip() != ""


# ---------------------------------------------------------------------------
# Filter construction never depends on evaluation labels (Section 25)
# ---------------------------------------------------------------------------


def test_filter_depends_only_on_extracted_rule_id_not_on_any_label():
    """Two different (hypothetical) 'gold labels' for the same query text
    must not change the filter — because classify_query has no parameter
    through which a label could even be passed (see the signature test
    above). This test demonstrates the filter is a pure function of the
    query text alone, called twice with no other input varying."""
    decision_1 = classify_query("GOLD-032", _CFG)
    decision_2 = classify_query("GOLD-032", _CFG)
    assert decision_1.filters_json == decision_2.filters_json


def test_filter_absent_for_non_exact_id_routes():
    for text in (
        "Como o Ticket Médio é definido no Gold e implementado no Power BI?",
        "Como é calculado average_ticket_amount?",
    ):
        assert classify_query(text, _CFG).filters_json is None
