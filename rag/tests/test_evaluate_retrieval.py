"""Unit tests for rag/src/evaluation/evaluate_retrieval.py.

Pure Python only — this file must never import databricks-sdk or make any
network call, matching the module under test.

Includes the mandatory Phase 4A.1 regression test for the BR-03
("GOLD-032") false-success bug (see module docstring in
evaluate_retrieval.py), plus the required evidence-matching test matrix.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from src.evaluation.evaluate_retrieval import (
    EvaluationDatasetError,
    EvalQuery,
    ExpectedEvidence,
    RankedResult,
    dcg_at_k,
    evidence_coverage_at_k,
    evidence_satisfied,
    first_relevant_rank,
    generic_heading_hit,
    grade_relevance,
    hit_at_k,
    load_eval_dataset,
    mrr,
    ndcg_at_k,
    precision_at_k,
)


def _evidence(**overrides) -> ExpectedEvidence:
    defaults = dict(
        evidence_id="ev-1",
        document_id="doc_a",
        rule_ids_any=("GOLD-001",),
        section_cues_any=("Some Section",),
    )
    defaults.update(overrides)
    return ExpectedEvidence(**defaults)


def _query(**overrides) -> EvalQuery:
    defaults = dict(
        query_id="Q-1",
        query="example query",
        category="business_rules",
        kind="direct",
        expected_evidence=(_evidence(),),
        multi_evidence_required=False,
    )
    defaults.update(overrides)
    return EvalQuery(**defaults)


def _result(**overrides) -> RankedResult:
    defaults = dict(
        rank=1,
        document_id="doc_a",
        section_path="Foo > Some Section",
        section_title="Some Section",
        rule_ids=("GOLD-001",),
        score=1.0,
    )
    defaults.update(overrides)
    return RankedResult(**defaults)


# ---------------------------------------------------------------------------
# BR-03 mandatory regression test (Section 18 of the Phase 4A.1 task)
# ---------------------------------------------------------------------------


def test_br03_regression_wrong_section_is_not_a_hit_right_section_is():
    """Reproduces the exact Phase 4A false-success bug and proves the fix:
    a same-document, wrong-section result (rank 1, generic 'Objetivo',
    no rule_ids) must NOT count as Hit@1 / EvidenceCoverage@1 / an MRR
    hit, but the actual GOLD-032 chunk (rank 2) must."""
    query = EvalQuery(
        query_id="BR-03",
        query="GOLD-032",
        category="business_rules",
        kind="identifier",
        expected_evidence=(
            ExpectedEvidence(
                evidence_id="gold_open_purchase_order_definition",
                document_id="gold_business_rules",
                rule_ids_any=("GOLD-032",),
                section_cues_any=("Definição de pedido de compra em aberto",),
            ),
        ),
        multi_evidence_required=False,
    )

    wrong_section = RankedResult(
        rank=1,
        document_id="gold_business_rules",
        section_path="Objetivo",
        section_title="Objetivo",
        rule_ids=(),
        score=0.79,
    )
    right_section = RankedResult(
        rank=2,
        document_id="gold_business_rules",
        section_path="Fornecedores > Definição de pedido de compra em aberto",
        section_title="Definição de pedido de compra em aberto",
        rule_ids=("GOLD-032",),
        score=0.76,
    )

    # Rank-1-only view: must be a complete miss.
    assert hit_at_k(query, [wrong_section], k=1) is False
    assert evidence_coverage_at_k(query, [wrong_section], k=1) == 0.0
    assert grade_relevance(query, wrong_section) == 0

    # Both ranks present: rank 2 is what makes it a hit.
    both = [wrong_section, right_section]
    assert hit_at_k(query, both, k=1) is False
    assert hit_at_k(query, both, k=2) is True
    assert evidence_coverage_at_k(query, both, k=1) == 0.0
    assert evidence_coverage_at_k(query, both, k=2) == 1.0
    assert grade_relevance(query, right_section) == 2
    assert first_relevant_rank(query, both) == 2
    assert mrr([(query, both)]) == pytest.approx(1 / 2)


# ---------------------------------------------------------------------------
# load_eval_dataset (v2 schema)
# ---------------------------------------------------------------------------


def _write_jsonl(tmp_path: Path, lines: list[str]) -> Path:
    path = tmp_path / "dataset.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


_VALID_EVIDENCE = {
    "evidence_id": "ev-1",
    "document_id": "doc_a",
    "rule_ids_any": ["GOLD-001"],
    "section_cues_any": ["Some Section"],
}

_VALID_ITEM = {
    "query_id": "Q-1",
    "query": "example",
    "category": "business_rules",
    "kind": "direct",
    "expected_evidence": [_VALID_EVIDENCE],
    "multi_evidence_required": False,
}


def test_load_eval_dataset_reads_valid_items(tmp_path: Path):
    path = _write_jsonl(tmp_path, [json.dumps(_VALID_ITEM)])
    queries = load_eval_dataset(path)
    assert len(queries) == 1
    assert queries[0].query_id == "Q-1"
    assert queries[0].expected_evidence[0].document_id == "doc_a"
    assert queries[0].expected_document_ids == ("doc_a",)


def test_load_eval_dataset_skips_blank_lines(tmp_path: Path):
    path = _write_jsonl(tmp_path, ["", json.dumps(_VALID_ITEM), "   "])
    assert len(load_eval_dataset(path)) == 1


def test_load_eval_dataset_preserves_file_order(tmp_path: Path):
    item_a = dict(_VALID_ITEM, query_id="Q-A")
    item_b = dict(_VALID_ITEM, query_id="Q-B")
    item_c = dict(_VALID_ITEM, query_id="Q-C")
    path = _write_jsonl(tmp_path, [json.dumps(item_a), json.dumps(item_b), json.dumps(item_c)])
    queries = load_eval_dataset(path)
    assert [q.query_id for q in queries] == ["Q-A", "Q-B", "Q-C"]


def test_load_eval_dataset_deterministic_across_runs(tmp_path: Path):
    item_a = dict(_VALID_ITEM, query_id="Q-A")
    item_b = dict(_VALID_ITEM, query_id="Q-B")
    path = _write_jsonl(tmp_path, [json.dumps(item_a), json.dumps(item_b)])
    first = [q.query_id for q in load_eval_dataset(path)]
    second = [q.query_id for q in load_eval_dataset(path)]
    assert first == second == ["Q-A", "Q-B"]


def test_load_eval_dataset_rejects_invalid_json(tmp_path: Path):
    path = _write_jsonl(tmp_path, ["{not valid json"])
    with pytest.raises(EvaluationDatasetError):
        load_eval_dataset(path)


def test_load_eval_dataset_rejects_missing_field(tmp_path: Path):
    item = dict(_VALID_ITEM)
    del item["category"]
    path = _write_jsonl(tmp_path, [json.dumps(item)])
    with pytest.raises(EvaluationDatasetError):
        load_eval_dataset(path)


def test_load_eval_dataset_rejects_unknown_category(tmp_path: Path):
    item = dict(_VALID_ITEM, category="not_a_real_category")
    path = _write_jsonl(tmp_path, [json.dumps(item)])
    with pytest.raises(EvaluationDatasetError):
        load_eval_dataset(path)


def test_load_eval_dataset_rejects_duplicate_query_id(tmp_path: Path):
    path = _write_jsonl(tmp_path, [json.dumps(_VALID_ITEM), json.dumps(_VALID_ITEM)])
    with pytest.raises(EvaluationDatasetError, match="duplicate query_id"):
        load_eval_dataset(path)


def test_load_eval_dataset_rejects_non_bool_multi_evidence(tmp_path: Path):
    item = dict(_VALID_ITEM, multi_evidence_required="true")
    path = _write_jsonl(tmp_path, [json.dumps(item)])
    with pytest.raises(EvaluationDatasetError):
        load_eval_dataset(path)


def test_load_eval_dataset_rejects_multi_evidence_with_one_item(tmp_path: Path):
    item = dict(_VALID_ITEM, multi_evidence_required=True, expected_evidence=[_VALID_EVIDENCE])
    path = _write_jsonl(tmp_path, [json.dumps(item)])
    with pytest.raises(EvaluationDatasetError, match="multi_evidence_required"):
        load_eval_dataset(path)


def test_load_eval_dataset_accepts_multi_evidence_with_two_items(tmp_path: Path):
    second = dict(_VALID_EVIDENCE, evidence_id="ev-2", document_id="doc_b")
    item = dict(
        _VALID_ITEM,
        multi_evidence_required=True,
        expected_evidence=[_VALID_EVIDENCE, second],
    )
    path = _write_jsonl(tmp_path, [json.dumps(item)])
    queries = load_eval_dataset(path)
    assert queries[0].multi_evidence_required is True
    assert len(queries[0].expected_evidence) == 2


def test_load_eval_dataset_rejects_duplicate_evidence_id(tmp_path: Path):
    dup = dict(_VALID_EVIDENCE)
    item = dict(_VALID_ITEM, expected_evidence=[_VALID_EVIDENCE, dup])
    path = _write_jsonl(tmp_path, [json.dumps(item)])
    with pytest.raises(EvaluationDatasetError, match="duplicate evidence_id"):
        load_eval_dataset(path)


def test_load_eval_dataset_rejects_malformed_evidence_missing_field(tmp_path: Path):
    bad_evidence = {"evidence_id": "ev-1", "document_id": "doc_a", "rule_ids_any": []}
    item = dict(_VALID_ITEM, expected_evidence=[bad_evidence])
    path = _write_jsonl(tmp_path, [json.dumps(item)])
    with pytest.raises(EvaluationDatasetError, match="section_cues_any"):
        load_eval_dataset(path)


def test_load_eval_dataset_rejects_evidence_not_an_object(tmp_path: Path):
    item = dict(_VALID_ITEM, expected_evidence=["not-an-object"])
    path = _write_jsonl(tmp_path, [json.dumps(item)])
    with pytest.raises(EvaluationDatasetError):
        load_eval_dataset(path)


def test_load_eval_dataset_negative_query_has_empty_evidence(tmp_path: Path):
    item = dict(_VALID_ITEM, category="negative", kind="negative", expected_evidence=[])
    path = _write_jsonl(tmp_path, [json.dumps(item)])
    queries = load_eval_dataset(path)
    assert queries[0].expected_evidence == ()


def test_load_eval_dataset_defaults_notes_to_empty_string(tmp_path: Path):
    item = dict(_VALID_ITEM)
    path = _write_jsonl(tmp_path, [json.dumps(item)])
    assert load_eval_dataset(path)[0].notes == ""


# ---------------------------------------------------------------------------
# evidence_satisfied — the core Phase 4A.1 matching rule
# ---------------------------------------------------------------------------


def test_evidence_satisfied_true_on_rule_id_match():
    e = _evidence(rule_ids_any=("GOLD-001",), section_cues_any=("Nonexistent",))
    r = _result(document_id="doc_a", rule_ids=("GOLD-001",), section_title="Unrelated")
    assert evidence_satisfied(e, r) is True


def test_evidence_satisfied_true_on_section_cue_match():
    e = _evidence(rule_ids_any=(), section_cues_any=("Some Section",))
    r = _result(document_id="doc_a", rule_ids=(), section_title="Some Section")
    assert evidence_satisfied(e, r) is True


def test_evidence_satisfied_section_cue_is_case_and_whitespace_normalized():
    e = _evidence(rule_ids_any=(), section_cues_any=("  SOME   SECTION  ",))
    r = _result(document_id="doc_a", rule_ids=(), section_title="some section")
    assert evidence_satisfied(e, r) is True


def test_evidence_satisfied_false_same_document_wrong_section():
    """Section 19: same document, wrong section -> no evidence match."""
    e = _evidence(rule_ids_any=("GOLD-999",), section_cues_any=("Nonexistent Section",))
    r = _result(document_id="doc_a", rule_ids=(), section_title="Objetivo")
    assert evidence_satisfied(e, r) is False


def test_evidence_satisfied_false_wrong_document():
    e = _evidence(document_id="doc_a")
    r = _result(document_id="doc_b", rule_ids=("GOLD-001",), section_title="Some Section")
    assert evidence_satisfied(e, r) is False


def test_evidence_satisfied_false_wrong_rule_id_correct_document():
    """Section 19: wrong Rule ID in correct document -> no match."""
    e = _evidence(rule_ids_any=("GOLD-032",), section_cues_any=())
    r = _result(document_id="doc_a", rule_ids=("GOLD-999",), section_title="Unrelated")
    assert evidence_satisfied(e, r) is False


def test_evidence_satisfied_section_cue_alone_without_rule_id_matches():
    """Section 19: correct section cue without Rule ID -> match."""
    e = _evidence(rule_ids_any=(), section_cues_any=("Some Section",))
    r = _result(document_id="doc_a", rule_ids=(), section_title="Some Section")
    assert evidence_satisfied(e, r) is True


def test_evidence_satisfied_whole_document_evidence_matches_on_document_alone():
    e = _evidence(rule_ids_any=(), section_cues_any=())
    r = _result(document_id="doc_a", rule_ids=(), section_title="Anything At All")
    assert evidence_satisfied(e, r) is True


def test_evidence_satisfied_whole_document_evidence_still_requires_correct_document():
    e = _evidence(document_id="doc_a", rule_ids_any=(), section_cues_any=())
    r = _result(document_id="doc_b")
    assert evidence_satisfied(e, r) is False


# ---------------------------------------------------------------------------
# grade_relevance — corrected: never derived from document match alone
# ---------------------------------------------------------------------------


def test_grade_relevance_two_on_satisfied_evidence():
    q = _query(expected_evidence=(_evidence(rule_ids_any=("GOLD-001",)),))
    r = _result(document_id="doc_a", rule_ids=("GOLD-001",))
    assert grade_relevance(q, r) == 2


def test_grade_relevance_zero_for_same_document_wrong_section_no_cross_reference():
    """The exact BR-03 shortcut this task forbids: same expected document
    must be able to grade 0 when the discriminant isn't met and there is
    no cross-referenced rule_id either."""
    q = _query(expected_evidence=(_evidence(rule_ids_any=("GOLD-032",), section_cues_any=("Specific Section",)),))
    r = _result(document_id="doc_a", rule_ids=(), section_title="Objetivo")
    assert grade_relevance(q, r) == 0


def test_grade_relevance_one_on_cross_referenced_rule_id_wrong_document():
    q = _query(expected_evidence=(_evidence(document_id="doc_a", rule_ids_any=("GOLD-003",)),))
    r = _result(document_id="doc_b", rule_ids=("GOLD-003",), section_title="Cross-reference note")
    assert grade_relevance(q, r) == 1


def test_grade_relevance_zero_for_negative_query():
    q = _query(expected_evidence=())
    r = _result(document_id="doc_a", rule_ids=("GOLD-001",))
    assert grade_relevance(q, r) == 0


# ---------------------------------------------------------------------------
# hit_at_k
# ---------------------------------------------------------------------------


def test_hit_at_k_true_when_evidence_satisfied_within_k():
    q = _query()
    results = [
        _result(rank=1, document_id="doc_x"),
        _result(rank=2, document_id="doc_a", rule_ids=("GOLD-001",)),
    ]
    assert hit_at_k(q, results, k=2) is True


def test_hit_at_k_false_when_only_wrong_section_within_k():
    q = _query(expected_evidence=(_evidence(rule_ids_any=("GOLD-001",), section_cues_any=("Some Section",)),))
    results = [
        _result(rank=1, document_id="doc_a", rule_ids=(), section_path="Objetivo", section_title="Objetivo")
    ]
    assert hit_at_k(q, results, k=1) is False


def test_hit_at_k_raises_for_negative_query():
    q = _query(expected_evidence=())
    with pytest.raises(ValueError):
        hit_at_k(q, [_result()], k=5)


# ---------------------------------------------------------------------------
# first_relevant_rank / mrr
# ---------------------------------------------------------------------------


def test_first_relevant_rank_skips_document_only_matches():
    q = _query(expected_evidence=(_evidence(rule_ids_any=("GOLD-001",), section_cues_any=("Some Section",)),))
    results = [
        _result(rank=1, document_id="doc_a", rule_ids=(), section_path="Objetivo", section_title="Objetivo"),
        _result(rank=2, document_id="doc_a", rule_ids=("GOLD-001",), section_title="Some Section"),
    ]
    assert first_relevant_rank(q, results) == 2


def test_first_relevant_rank_none_when_nothing_satisfies():
    q = _query()
    results = [_result(rank=1, document_id="doc_x")]
    assert first_relevant_rank(q, results) is None


def test_mrr_averages_reciprocal_ranks():
    q1 = _query(query_id="Q-1")
    q2 = _query(query_id="Q-2")
    results1 = [_result(rank=1, document_id="doc_a", rule_ids=("GOLD-001",))]
    results2 = [
        _result(rank=1, document_id="doc_x"),
        _result(rank=2, document_id="doc_a", rule_ids=("GOLD-001",)),
    ]
    value = mrr([(q1, results1), (q2, results2)])
    assert value == pytest.approx((1 / 1 + 1 / 2) / 2)


def test_mrr_skips_negative_queries():
    positive = _query(query_id="Q-1")
    negative = _query(query_id="Q-2", expected_evidence=())
    results_pos = [_result(rank=1, document_id="doc_a", rule_ids=("GOLD-001",))]
    results_neg = [_result(rank=1, document_id="doc_a")]
    value = mrr([(positive, results_pos), (negative, results_neg)])
    assert value == pytest.approx(1.0)


def test_mrr_empty_input_returns_zero():
    assert mrr([]) == 0.0


# ---------------------------------------------------------------------------
# evidence_coverage_at_k
# ---------------------------------------------------------------------------


def test_evidence_coverage_full_when_both_evidence_units_satisfied():
    q = _query(
        expected_evidence=(
            _evidence(evidence_id="ev-a", document_id="doc_a"),
            _evidence(evidence_id="ev-b", document_id="doc_b"),
        ),
        multi_evidence_required=True,
    )
    results = [
        _result(rank=1, document_id="doc_a", rule_ids=("GOLD-001",)),
        _result(rank=2, document_id="doc_b", rule_ids=("GOLD-001",)),
    ]
    assert evidence_coverage_at_k(q, results, k=2) == pytest.approx(1.0)


def test_evidence_coverage_half_when_only_one_evidence_unit_satisfied():
    """Section 13 example: expected = A + B, Top-K has only A satisfied
    (even if A appears multiple times) -> coverage 0.5, not 1.0."""
    q = _query(
        expected_evidence=(
            _evidence(evidence_id="ev-a", document_id="doc_a"),
            _evidence(evidence_id="ev-b", document_id="doc_b"),
        ),
        multi_evidence_required=True,
    )
    results = [
        _result(rank=1, document_id="doc_a", rule_ids=("GOLD-001",)),
        _result(rank=2, document_id="doc_a", rule_ids=("GOLD-001",)),
        _result(rank=3, document_id="doc_a", rule_ids=("GOLD-001",)),
        _result(
            rank=4,
            document_id="doc_b",
            rule_ids=(),
            section_path="Unrelated section",
            section_title="Unrelated section",
        ),
    ]
    assert evidence_coverage_at_k(q, results, k=4) == pytest.approx(0.5)


def test_evidence_coverage_deduplicates_repeated_satisfaction_of_same_unit():
    q = _query(expected_evidence=(_evidence(evidence_id="ev-a", document_id="doc_a"),))
    results = [
        _result(rank=1, document_id="doc_a", rule_ids=("GOLD-001",)),
        _result(rank=2, document_id="doc_a", rule_ids=("GOLD-001",)),
    ]
    assert evidence_coverage_at_k(q, results, k=2) == pytest.approx(1.0)


def test_evidence_coverage_zero_when_neither_satisfied():
    q = _query(
        expected_evidence=(
            _evidence(evidence_id="ev-a", document_id="doc_a"),
            _evidence(evidence_id="ev-b", document_id="doc_b"),
        ),
        multi_evidence_required=True,
    )
    results = [_result(rank=1, document_id="doc_z")]
    assert evidence_coverage_at_k(q, results, k=1) == pytest.approx(0.0)


def test_evidence_coverage_respects_k_boundary():
    q = _query(
        expected_evidence=(
            _evidence(evidence_id="ev-a", document_id="doc_a"),
            _evidence(evidence_id="ev-b", document_id="doc_b"),
        ),
        multi_evidence_required=True,
    )
    results = [
        _result(rank=1, document_id="doc_a", rule_ids=("GOLD-001",)),
        _result(rank=9, document_id="doc_b", rule_ids=("GOLD-001",)),
    ]
    assert evidence_coverage_at_k(q, results, k=3) == pytest.approx(0.5)
    assert evidence_coverage_at_k(q, results, k=9) == pytest.approx(1.0)


def test_evidence_coverage_raises_for_negative_query():
    q = _query(expected_evidence=())
    with pytest.raises(ValueError):
        evidence_coverage_at_k(q, [_result()], k=5)


# ---------------------------------------------------------------------------
# precision_at_k
# ---------------------------------------------------------------------------


def test_precision_at_k_counts_grade_one_and_two_as_relevant():
    q = _query(expected_evidence=(_evidence(document_id="doc_a", rule_ids_any=("GOLD-001",), section_cues_any=()),))
    results = [
        _result(rank=1, document_id="doc_a", rule_ids=("GOLD-001",)),  # grade 2
        _result(rank=2, document_id="doc_b", rule_ids=("GOLD-001",)),  # grade 1 (cross-ref)
        _result(rank=3, document_id="doc_x", rule_ids=()),  # grade 0
    ]
    assert precision_at_k(q, results, k=3) == pytest.approx(2 / 3)


def test_precision_at_k_empty_results_returns_zero():
    q = _query()
    assert precision_at_k(q, [], k=5) == 0.0


# ---------------------------------------------------------------------------
# dcg_at_k / ndcg_at_k
# ---------------------------------------------------------------------------


def test_dcg_at_k_matches_hand_computed_value():
    value = dcg_at_k([2, 1, 0], k=3)
    expected = 2 / math.log2(2) + 1 / math.log2(3) + 0 / math.log2(4)
    assert value == pytest.approx(expected)


def test_ndcg_at_k_is_one_for_perfectly_ranked_grades():
    assert ndcg_at_k([2, 1, 0], k=3) == pytest.approx(1.0)


def test_ndcg_at_k_less_than_one_for_suboptimal_ranking():
    value = ndcg_at_k([0, 1, 2], k=3)
    assert 0.0 < value < 1.0


def test_ndcg_at_k_zero_when_no_relevant_grades():
    assert ndcg_at_k([0, 0, 0], k=3) == 0.0


def test_ndcg_at_k_is_deterministic():
    grades = [2, 0, 1, 2, 0]
    assert ndcg_at_k(grades, k=5) == ndcg_at_k(grades, k=5)


# ---------------------------------------------------------------------------
# generic_heading_hit
# ---------------------------------------------------------------------------


def test_generic_heading_hit_true_for_known_generic_heading():
    r = _result(section_title="Objetivo")
    assert generic_heading_hit(r) is True


def test_generic_heading_hit_false_for_specific_heading():
    r = _result(section_title="Rateio proporcional de desconto do pedido")
    assert generic_heading_hit(r) is False


def test_generic_heading_hit_falls_back_to_last_section_path_segment():
    r = _result(section_title=None, section_path="Vendas > Objetivo")
    assert generic_heading_hit(r) is True


def test_generic_heading_hit_false_when_no_heading_information():
    r = _result(section_title=None, section_path=None)
    assert generic_heading_hit(r) is False


def test_generic_heading_hit_is_case_insensitive():
    r = _result(section_title="OBJETIVO E ESCOPO")
    assert generic_heading_hit(r) is True
