"""Phase 4A.1 — offline, evidence-level recomputation of the Phase 4A
baseline.

Deliberately makes **zero** new AI Search calls: it re-reads the exact
Top-15 retrieval results already collected and persisted by Phase 4A
(``rag/evaluation/results/baseline_v1/raw_results.jsonl``) and re-scores
them against the corrected ``retrieval_queries_v2.jsonl`` dataset
(``expected_evidence``, not ``expected_document_ids``) using the
corrected metric functions in ``evaluate_retrieval.py``.

``raw_results.jsonl`` does not persist ``chunk_text`` — only
``chunk_id``, ``document_id``, ``section_path``, ``section_title``,
``rule_ids``, ``score``, ``rank`` (plus the old, now-discarded
document-level ``grade``/``generic_heading`` fields). This is sufficient
for the corrected methodology: evidence matching is defined over
``rule_ids`` (exact) and ``section_path``/``section_title`` (normalized),
never over raw chunk text (see ``evaluate_retrieval.evidence_satisfied``),
so no new retrieval call is needed to recover missing information.

This script has no Databricks dependency and could in principle be unit
tested, but it is a thin I/O + aggregation driver over already-tested
pure functions (see ``test_evaluate_retrieval.py``) — consistent with
how ``run_baseline.py`` (the live-retrieval driver) is not unit tested
either.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

from src.evaluation.evaluate_retrieval import (
    EvalQuery,
    RankedResult,
    evidence_coverage_at_k,
    generic_heading_hit,
    grade_relevance,
    hit_at_k,
    load_eval_dataset,
    mrr,
    ndcg_at_k,
    precision_at_k,
)

_K_VALUES = (3, 5, 8, 10, 15)

_ROOT = Path(__file__).resolve().parents[2]
_DATASET_V2_PATH = _ROOT / "evaluation" / "dataset" / "retrieval_queries_v2.jsonl"
_RAW_RESULTS_V1_PATH = _ROOT / "evaluation" / "results" / "baseline_v1" / "raw_results.jsonl"
_RESULTS_DIR = _ROOT / "evaluation" / "results" / "baseline_v1_corrected"


def _load_raw_results(path: Path) -> dict[str, list[RankedResult]]:
    per_query: dict[str, list[RankedResult]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        per_query[row["query_id"]] = [
            RankedResult(
                rank=r["rank"],
                document_id=r["document_id"],
                section_path=r["section_path"],
                section_title=r["section_title"],
                rule_ids=tuple(r["rule_ids"]),
                score=r["score"],
            )
            for r in row["results"]
        ]
    return per_query


def recompute(
    dataset_path: Path = _DATASET_V2_PATH,
    raw_results_path: Path = _RAW_RESULTS_V1_PATH,
    results_dir: Path = _RESULTS_DIR,
) -> None:
    queries = load_eval_dataset(dataset_path)
    raw_by_query = _load_raw_results(raw_results_path)

    missing = [q.query_id for q in queries if q.query_id not in raw_by_query]
    if missing:
        raise RuntimeError(
            f"No persisted Phase 4A raw results for query_id(s) {missing} — "
            "cannot recompute offline for these without a new retrieval call."
        )

    per_query_results = {q.query_id: raw_by_query[q.query_id] for q in queries}

    graded_rows = []
    for q in queries:
        results = per_query_results[q.query_id]
        graded_rows.append(
            {
                "query_id": q.query_id,
                "query": q.query,
                "category": q.category,
                "expected_evidence": [
                    {
                        "evidence_id": e.evidence_id,
                        "document_id": e.document_id,
                        "rule_ids_any": list(e.rule_ids_any),
                        "section_cues_any": list(e.section_cues_any),
                    }
                    for e in q.expected_evidence
                ],
                "results": [
                    {
                        "rank": r.rank,
                        "document_id": r.document_id,
                        "section_path": r.section_path,
                        "section_title": r.section_title,
                        "rule_ids": list(r.rule_ids),
                        "score": r.score,
                        "grade_corrected": grade_relevance(q, r),
                        "generic_heading": generic_heading_hit(r),
                    }
                    for r in results
                ],
            }
        )

    results_dir.mkdir(parents=True, exist_ok=True)
    graded_path = results_dir / "raw_results_graded.jsonl"
    with graded_path.open("w", encoding="utf-8") as f:
        for row in graded_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    summary = _build_summary(queries, per_query_results)
    summary_path = results_dir / "metrics_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Wrote {graded_path}")
    print(f"Wrote {summary_path}")


def _build_summary(
    queries: list[EvalQuery], per_query_results: dict[str, list[RankedResult]]
) -> dict:
    positive_queries = [q for q in queries if q.expected_evidence]
    negative_queries = [q for q in queries if not q.expected_evidence]

    by_k: dict[int, dict] = {}
    for k in _K_VALUES:
        hits = [hit_at_k(q, per_query_results[q.query_id], k) for q in positive_queries]
        coverages = [
            evidence_coverage_at_k(q, per_query_results[q.query_id], k) for q in positive_queries
        ]
        precisions = [
            precision_at_k(q, per_query_results[q.query_id], k) for q in positive_queries
        ]
        ndcgs = []
        for q in positive_queries:
            top_k = [r for r in per_query_results[q.query_id] if r.rank <= k]
            grades = [grade_relevance(q, r) for r in sorted(top_k, key=lambda r: r.rank)]
            ndcgs.append(ndcg_at_k(grades, k))

        by_k[k] = {
            "hit_rate": sum(hits) / len(hits) if hits else 0.0,
            "mean_evidence_coverage": statistics.fmean(coverages) if coverages else 0.0,
            "mean_precision": statistics.fmean(precisions) if precisions else 0.0,
            "mean_ndcg": statistics.fmean(ndcgs) if ndcgs else 0.0,
        }

    overall_mrr = mrr([(q, per_query_results[q.query_id]) for q in positive_queries])

    by_category: dict[str, dict] = {}
    for category in sorted({q.category for q in queries}):
        cat_queries = [q for q in queries if q.category == category]
        cat_positive = [q for q in cat_queries if q.expected_evidence]
        entry: dict = {"query_count": len(cat_queries)}
        if cat_positive:
            entry["mrr"] = mrr([(q, per_query_results[q.query_id]) for q in cat_positive])
            entry["hit_rate_at_5"] = statistics.fmean(
                [1.0 if hit_at_k(q, per_query_results[q.query_id], 5) else 0.0 for q in cat_positive]
            )
            entry["mean_evidence_coverage_at_5"] = statistics.fmean(
                [evidence_coverage_at_k(q, per_query_results[q.query_id], 5) for q in cat_positive]
            )
        by_category[category] = entry

    negative_top1_scores = [
        per_query_results[q.query_id][0].score for q in negative_queries if per_query_results[q.query_id]
    ]
    positive_top1_scores = [
        per_query_results[q.query_id][0].score for q in positive_queries if per_query_results[q.query_id]
    ]

    generic_heading_counts = {"top_3": 0, "top_5": 0}
    for q in queries:
        for r in per_query_results[q.query_id]:
            if generic_heading_hit(r):
                if r.rank <= 3:
                    generic_heading_counts["top_3"] += 1
                if r.rank <= 5:
                    generic_heading_counts["top_5"] += 1

    # False-success audit: cases where the expected document appears in
    # Top-K (old, document-level definition) but no expected evidence is
    # actually satisfied (corrected definition) — i.e. exactly the BR-03
    # bug pattern, found systematically rather than by inspection alone.
    false_successes = []
    for q in positive_queries:
        results = per_query_results[q.query_id]
        expected_docs = {e.document_id for e in q.expected_evidence}
        for k in (5, 10, 15):
            top_k = [r for r in results if r.rank <= k]
            doc_level_hit = any(r.document_id in expected_docs for r in top_k)
            evidence_level_hit = hit_at_k(q, results, k)
            if doc_level_hit and not evidence_level_hit:
                false_successes.append({"query_id": q.query_id, "k": k})

    return {
        "dataset_size": len(queries),
        "positive_queries": len(positive_queries),
        "negative_queries": len(negative_queries),
        "by_k": by_k,
        "overall_mrr": overall_mrr,
        "by_category": by_category,
        "score_analysis": {
            "positive_top1_scores": positive_top1_scores,
            "negative_top1_scores": negative_top1_scores,
        },
        "generic_heading_hits": generic_heading_counts,
        "document_level_false_successes": false_successes,
    }


if __name__ == "__main__":
    recompute()
