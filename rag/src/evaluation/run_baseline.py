"""Phase 4A retrieval baseline runner.

Databricks-dependent driver (unlike ``evaluate_retrieval.py``, which is
pure Python): loads the evaluation dataset, issues exactly one
``num_results=15`` AI Search query per query (deriving Top-3/5/8/10
prefixes from that single call — see ``derive_prefixes``), grades and
scores results, and writes versioned outputs.

Read-only against Databricks: never syncs, never writes to
``document_chunks``, never touches the index configuration. Not unit
tested directly (matches project convention — Databricks-dependent
modules are validated by running them, not by local unit tests; see
``rag/README.md`` "Testing boundary").

Usage (from ``rag/``, with a Databricks CLI profile configured):

    python3 -m src.evaluation.run_baseline --profile lh-nautical
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from typing import Sequence

from databricks.sdk import WorkspaceClient

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
from src.retrieval.ai_search import RetrievedChunk, query_index

_INDEX_NAME = "dev_natalia_correia_lh_nautical.rag.document_chunks_index"
_MAX_K = 15
_K_VALUES = (3, 5, 8, 10, 15)

_DATASET_PATH = Path(__file__).resolve().parents[2] / "evaluation" / "dataset" / "retrieval_queries_v1.jsonl"
_RESULTS_DIR = Path(__file__).resolve().parents[2] / "evaluation" / "results" / "baseline_v1"


def _to_ranked_result(chunk: RetrievedChunk) -> RankedResult:
    return RankedResult(
        rank=chunk.rank,
        document_id=chunk.document_id,
        section_path=chunk.section_path,
        section_title=chunk.section_title,
        rule_ids=chunk.rule_ids,
        score=chunk.score,
    )


def derive_prefixes(results: Sequence[RankedResult], k_values: Sequence[int]) -> dict:
    """Derive Top-K prefixes from a single Top-15 pull.

    Valid because AI Search returns results already sorted by descending
    relevance rank in a single response — the Top-K prefix of a Top-15
    call is, by construction, identical to what a dedicated Top-K call
    would return (same query, same index state, same ranking function).
    No re-querying is needed per K.
    """
    return {k: [r for r in results if r.rank <= k] for k in k_values}


def run(profile: str, dataset_path: Path = _DATASET_PATH, results_dir: Path = _RESULTS_DIR) -> None:
    queries = load_eval_dataset(dataset_path)
    client = WorkspaceClient(profile=profile)

    raw_results: list[dict] = []
    latencies_ms: list[float] = []
    per_query_results: dict[str, list[RankedResult]] = {}

    for q in queries:
        start = time.monotonic()
        chunks = query_index(
            client,
            _INDEX_NAME,
            q.query,
            num_results=_MAX_K,
            query_type="HYBRID",
        )
        elapsed_ms = (time.monotonic() - start) * 1000.0
        latencies_ms.append(elapsed_ms)

        ranked = [_to_ranked_result(c) for c in chunks]
        per_query_results[q.query_id] = ranked

        raw_results.append(
            {
                "query_id": q.query_id,
                "query": q.query,
                "category": q.category,
                "latency_ms": round(elapsed_ms, 1),
                "results": [
                    {
                        "rank": c.rank,
                        "chunk_id": c.chunk_id,
                        "document_id": c.document_id,
                        "section_path": c.section_path,
                        "section_title": c.section_title,
                        "rule_ids": list(c.rule_ids),
                        "score": c.score,
                        "grade": grade_relevance(q, _to_ranked_result(c)),
                        "generic_heading": generic_heading_hit(_to_ranked_result(c)),
                    }
                    for c in chunks
                ],
            }
        )

    results_dir.mkdir(parents=True, exist_ok=True)

    raw_path = results_dir / "raw_results.jsonl"
    with raw_path.open("w", encoding="utf-8") as f:
        for row in raw_results:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    summary = _build_summary(queries, per_query_results, latencies_ms)
    summary_path = results_dir / "metrics_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Wrote {raw_path}")
    print(f"Wrote {summary_path}")


def _build_summary(
    queries: list[EvalQuery],
    per_query_results: dict[str, list[RankedResult]],
    latencies_ms: list[float],
) -> dict:
    positive_queries = [q for q in queries if q.expected_document_ids]
    negative_queries = [q for q in queries if not q.expected_document_ids]

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

    mrr_pairs = [(q, per_query_results[q.query_id]) for q in positive_queries]
    overall_mrr = mrr(mrr_pairs)

    by_category: dict[str, dict] = {}
    categories = sorted({q.category for q in queries})
    for category in categories:
        cat_queries = [q for q in queries if q.category == category]
        cat_positive = [q for q in cat_queries if q.expected_document_ids]
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
        per_query_results[q.query_id][0].score
        for q in negative_queries
        if per_query_results[q.query_id]
    ]
    positive_top1_scores = [
        per_query_results[q.query_id][0].score
        for q in positive_queries
        if per_query_results[q.query_id]
    ]

    generic_heading_counts = {"top_3": 0, "top_5": 0}
    for q in queries:
        for r in per_query_results[q.query_id]:
            if generic_heading_hit(r):
                if r.rank <= 3:
                    generic_heading_counts["top_3"] += 1
                if r.rank <= 5:
                    generic_heading_counts["top_5"] += 1

    latency_summary = {}
    if latencies_ms:
        sorted_lat = sorted(latencies_ms)
        latency_summary = {
            "count": len(sorted_lat),
            "mean_ms": round(statistics.fmean(sorted_lat), 1),
            "p50_ms": round(statistics.median(sorted_lat), 1),
            "p95_ms": round(sorted_lat[max(0, int(len(sorted_lat) * 0.95) - 1)], 1),
            "max_ms": round(max(sorted_lat), 1),
        }

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
        "latency_ms": latency_summary,
    }


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, help="Databricks CLI profile to use for auth.")
    parser.add_argument("--dataset", type=Path, default=_DATASET_PATH)
    parser.add_argument("--results-dir", type=Path, default=_RESULTS_DIR)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv)
    run(args.profile, dataset_path=args.dataset, results_dir=args.results_dir)


if __name__ == "__main__":
    main()
