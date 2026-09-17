"""Phase 4C — offline policy-level regression evaluation.

Makes **zero** new AI Search calls for the 37-query v2 set: each query is
classified by :func:`classify_query`, then graded using results already on
disk, truncated to the policy's own Top-K —

- ``EXACT_RULE_ID`` (BR-03 only): reuses Phase 4B Experiment A2's
  ``identifier_rule_filter_v1`` results (the actual filtered retrieval —
  not a truncation of the unfiltered HYBRID baseline, since a filter
  changes *which* candidates are returned, not just how many).
- ``CROSS_SOURCE`` / ``NORMAL``: reuses Phase 4A.1's corrected-baseline
  Top-15 HYBRID results (``baseline_v1/raw_results.jsonl``), truncated to
  the route's own ``top_k`` — valid because HYBRID Top-K is a prefix of a
  wider HYBRID pull for the same query and index state (same reasoning
  already used in Phase 4A's ``derive_prefixes``).

Negative/OOD queries are evaluated separately as a diagnostic (not part of
the Hit/Coverage/MRR aggregate), per the policy task's own instruction.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

from src.evaluation.evaluate_retrieval import (
    EvalQuery,
    RankedResult,
    evidence_coverage_at_k,
    grade_relevance,
    hit_at_k,
    load_eval_dataset,
    mrr,
    precision_at_k,
)
from src.retrieval.policy import DEFAULT_POLICY_CONFIG, RetrievalRoute, classify_query

_ROOT = Path(__file__).resolve().parents[2]
_DATASET_V2 = _ROOT / "evaluation" / "dataset" / "retrieval_queries_v2.jsonl"
_BASELINE_RAW = _ROOT / "evaluation" / "results" / "baseline_v1" / "raw_results.jsonl"
_IDENTIFIER_FILTER_RAW = _ROOT / "evaluation" / "results" / "identifier_rule_filter_v1" / "raw_results.jsonl"
_IDENTIFIER_DATASET = _ROOT / "evaluation" / "dataset" / "identifier_queries_v1.jsonl"
_OUT_DIR = _ROOT / "evaluation" / "results" / "policy_v1"


def _load_raw(path: Path) -> dict[str, list[RankedResult]]:
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


def run() -> None:
    queries = load_eval_dataset(_DATASET_V2)
    baseline_raw = _load_raw(_BASELINE_RAW)
    identifier_filter_raw = _load_raw(_IDENTIFIER_FILTER_RAW)

    # BR-03's identifier-route results come from the identifier dataset's
    # ID-03 (same query text, "GOLD-032"), not from a query_id named BR-03
    # in that file — bridge the two datasets explicitly, once.
    identifier_queries = {q.query_id: q for q in load_eval_dataset(_IDENTIFIER_DATASET)}
    assert identifier_queries["ID-03"].query == "GOLD-032"

    per_query_route: dict[str, RetrievalRoute] = {}
    per_query_results: dict[str, list[RankedResult]] = {}
    per_query_records: list[dict] = []

    for q in queries:
        decision = classify_query(q.query, DEFAULT_POLICY_CONFIG)
        per_query_route[q.query_id] = decision.route

        if decision.route == RetrievalRoute.EXACT_RULE_ID:
            source = "identifier_rule_filter_v1 (Phase 4B A2)"
            full_results = identifier_filter_raw["ID-03"]
        else:
            source = "baseline_v1 (Phase 4A.1 corrected, HYBRID Top-15, truncated)"
            full_results = baseline_raw[q.query_id]

        truncated = [r for r in full_results if r.rank <= decision.top_k]
        per_query_results[q.query_id] = truncated

        per_query_records.append(
            {
                "query_id": q.query_id,
                "query": q.query,
                "category": q.category,
                "route": decision.route.value,
                "top_k": decision.top_k,
                "filters_json": decision.filters_json,
                "provenance": source,
                "result_count": len(truncated),
            }
        )

    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    routes_path = _OUT_DIR / "per_query_routes.jsonl"
    with routes_path.open("w", encoding="utf-8") as f:
        for row in per_query_records:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    summary = _build_summary(queries, per_query_route, per_query_results)
    summary_path = _OUT_DIR / "policy_metrics_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Wrote {routes_path}")
    print(f"Wrote {summary_path}")


def _build_summary(
    queries: list[EvalQuery],
    per_query_route: dict[str, RetrievalRoute],
    per_query_results: dict[str, list[RankedResult]],
) -> dict:
    negative_ids = {q.query_id for q in queries if q.category == "negative"}
    positive_queries = [q for q in queries if q.expected_evidence and q.query_id not in negative_ids]

    def metrics_for(qlist: list[EvalQuery]) -> dict:
        if not qlist:
            return {}
        # top_k has already been applied via truncation in run(), so "k"
        # for these metric calls just needs to be large enough to include
        # everything truncated (never smaller than any real result count).
        k = 999
        hits = [hit_at_k(q, per_query_results[q.query_id], k) for q in qlist]
        covs = [evidence_coverage_at_k(q, per_query_results[q.query_id], k) for q in qlist]
        precs = [precision_at_k(q, per_query_results[q.query_id], k) for q in qlist]
        m = mrr([(q, per_query_results[q.query_id]) for q in qlist])
        return {
            "count": len(qlist),
            "hit_rate": sum(hits) / len(hits),
            "mean_evidence_coverage": statistics.fmean(covs),
            "mean_precision": statistics.fmean(precs),
            "mrr": m,
            "misses": [q.query_id for q, h in zip(qlist, hits) if not h],
        }

    by_route: dict[str, dict] = {}
    for route in RetrievalRoute:
        qlist = [q for q in positive_queries if per_query_route[q.query_id] == route]
        by_route[route.value] = metrics_for(qlist)

    overall = metrics_for(positive_queries)

    negative_summary = []
    for q in queries:
        if q.query_id not in negative_ids:
            continue
        results = per_query_results[q.query_id]
        negative_summary.append(
            {
                "query_id": q.query_id,
                "route": per_query_route[q.query_id].value,
                "top1_score": results[0].score if results else None,
                "top1_document": results[0].document_id if results else None,
                "result_count": len(results),
            }
        )

    return {
        "overall_positive": overall,
        "by_route": by_route,
        "negative_diagnostic": negative_summary,
    }


if __name__ == "__main__":
    run()
