"""Phase 4B — Experiment Group A: identifier-only retrieval.

Databricks-dependent driver (like ``run_baseline.py``): runs the
identifier query subset (``rag/evaluation/dataset/identifier_queries_v1.jsonl``)
under controlled, isolated configurations and writes one result directory
per configuration. Never combines two interventions in the same run.

Strict no-oracle-leakage rule: query construction (filter, expansion) uses
only ``src.retrieval.query_routing``, which never imports the evaluation
package — see ``rag/tests/test_query_routing.py``. ``EvalQuery.expected_evidence``
is used only afterwards, to grade results already returned by the service.

Usage (from ``rag/``):

    python3 -m src.evaluation.run_identifier_experiments --profile lh-nautical --experiment A0
    python3 -m src.evaluation.run_identifier_experiments --profile lh-nautical --experiment A1
    python3 -m src.evaluation.run_identifier_experiments --profile lh-nautical --experiment A2
    python3 -m src.evaluation.run_identifier_experiments --profile lh-nautical --experiment A3
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
    first_relevant_rank,
    grade_relevance,
    hit_at_k,
    load_eval_dataset,
    mrr,
)
from src.retrieval.ai_search import RetrievedChunk, query_index
from src.retrieval.query_routing import (
    build_rule_id_filter_json,
    expand_identifier_query,
    extract_single_rule_id,
)

_INDEX_NAME = "dev_natalia_correia_lh_nautical.rag.document_chunks_index"
_MAX_K = 15
_K_VALUES = (1, 3, 5, 8, 15)

_ROOT = Path(__file__).resolve().parents[2]
_DATASET_PATH = _ROOT / "evaluation" / "dataset" / "identifier_queries_v1.jsonl"

_EXPERIMENTS = {
    "A0": {"dir": "identifier_hybrid_v1", "query_type": "HYBRID", "filter": False, "expand": False},
    "A1": {"dir": "identifier_fulltext_v1", "query_type": "FULL_TEXT", "filter": False, "expand": False},
    "A2": {"dir": "identifier_rule_filter_v1", "query_type": "HYBRID", "filter": True, "expand": False},
    "A3": {"dir": "identifier_expansion_v1", "query_type": "HYBRID", "filter": False, "expand": True},
}


def _to_ranked(chunk: RetrievedChunk) -> RankedResult:
    return RankedResult(
        rank=chunk.rank,
        document_id=chunk.document_id,
        section_path=chunk.section_path,
        section_title=chunk.section_title,
        rule_ids=chunk.rule_ids,
        score=chunk.score,
    )


def run(profile: str, experiment: str, results_dir: Path | None = None) -> None:
    if experiment not in _EXPERIMENTS:
        raise ValueError(f"Unknown experiment {experiment!r}; expected one of {sorted(_EXPERIMENTS)}")
    cfg = _EXPERIMENTS[experiment]
    out_dir = results_dir or (_ROOT / "evaluation" / "results" / cfg["dir"])

    queries = load_eval_dataset(_DATASET_PATH)
    client = WorkspaceClient(profile=profile)

    raw_rows = []
    latencies_ms = []
    per_query_results: dict[str, list[RankedResult]] = {}
    skipped = []

    for q in queries:
        query_text = q.query
        filters_json = None

        if cfg["filter"] or cfg["expand"]:
            rule_id = extract_single_rule_id(q.query)
            if rule_id is None:
                skipped.append(q.query_id)
                continue
            if cfg["filter"]:
                filters_json = build_rule_id_filter_json(rule_id)
            if cfg["expand"]:
                query_text = expand_identifier_query(q.query, rule_id)

        start = time.monotonic()
        chunks = query_index(
            client,
            _INDEX_NAME,
            query_text,
            num_results=_MAX_K,
            query_type=cfg["query_type"],
            filters_json=filters_json,
        )
        elapsed_ms = (time.monotonic() - start) * 1000.0
        latencies_ms.append(elapsed_ms)

        ranked = [_to_ranked(c) for c in chunks]
        per_query_results[q.query_id] = ranked

        raw_rows.append(
            {
                "query_id": q.query_id,
                "query": q.query,
                "query_sent": query_text,
                "filters_json": filters_json,
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
                        "grade": grade_relevance(q, _to_ranked(c)),
                    }
                    for c in chunks
                ],
            }
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / "raw_results.jsonl"
    with raw_path.open("w", encoding="utf-8") as f:
        for row in raw_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    summary = _build_summary(queries, per_query_results, latencies_ms, skipped, cfg)
    summary_path = out_dir / "metrics_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"[{experiment}] calls made: {len(raw_rows)} | skipped (no single rule_id): {len(skipped)}")
    print(f"Wrote {raw_path}")
    print(f"Wrote {summary_path}")


def _build_summary(
    queries: list[EvalQuery],
    per_query_results: dict[str, list[RankedResult]],
    latencies_ms: list[float],
    skipped: list[str],
    cfg: dict,
) -> dict:
    scored_queries = [q for q in queries if q.query_id in per_query_results]

    by_k: dict[int, dict] = {}
    for k in _K_VALUES:
        hits = [hit_at_k(q, per_query_results[q.query_id], k) for q in scored_queries]
        coverages = [
            evidence_coverage_at_k(q, per_query_results[q.query_id], k) for q in scored_queries
        ]
        by_k[k] = {
            "hit_rate": sum(hits) / len(hits) if hits else 0.0,
            "mean_evidence_coverage": statistics.fmean(coverages) if coverages else 0.0,
        }

    overall_mrr = mrr([(q, per_query_results[q.query_id]) for q in scored_queries])

    exact_rule_ranks = {
        q.query_id: first_relevant_rank(q, per_query_results[q.query_id]) for q in scored_queries
    }

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

    result_counts = {qid: len(results) for qid, results in per_query_results.items()}

    return {
        "config": cfg,
        "dataset_size": len(queries),
        "scored_queries": len(scored_queries),
        "skipped_queries": skipped,
        "by_k": by_k,
        "overall_mrr": overall_mrr,
        "exact_evidence_first_rank": exact_rule_ranks,
        "result_counts": result_counts,
        "latency_ms": latency_summary,
    }


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--experiment", required=True, choices=sorted(_EXPERIMENTS))
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv)
    run(args.profile, args.experiment)


if __name__ == "__main__":
    main()
