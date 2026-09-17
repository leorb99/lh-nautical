"""Phase 4C — small runtime smoke test of the retrieval policy against the
live index. Exercises the full pipeline:

    query -> classify_query() -> RetrievalPolicyDecision -> ai_search.query_index() -> results

Exactly the 5 cases (A-E) specified by the Phase 4C task — not a full
37-query rerun (see ``run_policy_regression.py`` for the offline
policy-level regression, which reuses existing raw results instead).

Usage (from ``rag/``):

    python3 -m src.evaluation.run_policy_smoke_test --profile lh-nautical
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from databricks.sdk import WorkspaceClient

from src.retrieval.ai_search import query_index
from src.retrieval.policy import DEFAULT_POLICY_CONFIG, classify_query

_INDEX_NAME = "dev_natalia_correia_lh_nautical.rag.document_chunks_index"
_ROOT = Path(__file__).resolve().parents[2]
_OUT_PATH = _ROOT / "evaluation" / "results" / "policy_v1" / "smoke_test_results.json"

_CASES = [
    ("A", "GOLD-032"),
    ("B", "GOLD-999"),
    ("C", "Como o Power BI calcula a medida Ticket Médio?"),
    ("D", "Como o Ticket Médio é definido no Gold e implementado no Power BI?"),
    ("E", "O que acontece quando um mês não teve nenhuma venda registrada, no histórico usado para forecasting?"),
]


def run(profile: str, out_path: Path = _OUT_PATH) -> list[dict]:
    client = WorkspaceClient(profile=profile)
    rows = []

    for case_id, query_text in _CASES:
        decision = classify_query(query_text, DEFAULT_POLICY_CONFIG)
        chunks = query_index(
            client,
            _INDEX_NAME,
            query_text,
            num_results=decision.top_k,
            query_type=decision.query_type,
            filters_json=decision.filters_json,
        )
        rows.append(
            {
                "case_id": case_id,
                "query": query_text,
                "decision": {
                    "route": decision.route.value,
                    "query_type": decision.query_type,
                    "top_k": decision.top_k,
                    "filters_json": decision.filters_json,
                    "extracted_rule_id": decision.extracted_rule_id,
                    "reason": decision.reason,
                },
                "result_count": len(chunks),
                "results": [
                    {
                        "rank": c.rank,
                        "document_id": c.document_id,
                        "section_path": c.section_path,
                        "rule_ids": list(c.rule_ids),
                        "score": c.score,
                    }
                    for c in chunks
                ],
            }
        )
        print(
            f"[{case_id}] {query_text!r} -> route={decision.route.value} "
            f"top_k={decision.top_k} filter={decision.filters_json} "
            f"result_count={len(chunks)}"
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {out_path}")
    return rows


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv)
    run(args.profile)


if __name__ == "__main__":
    main()
