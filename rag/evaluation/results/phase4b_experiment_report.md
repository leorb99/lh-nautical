# Phase 4B — Controlled Retrieval Experiments Report

Reference baseline: `rag/evaluation/results/baseline_v1_corrected/` (evidence-level,
Phase 4A.1). No production retrieval policy changed. Read-only against
Databricks (queries only; no ingestion/sync/index mutation).

## Platform capability check (read-only probes)

| Capability | Result |
|---|---|
| `query_type=FULL_TEXT` | **AVAILABLE** — confirmed via a live query |
| Metadata filter on `rule_ids ARRAY<STRING>` (`{"rule_ids": "<value>"}`) | **AVAILABLE** — confirmed positive (returns matching rows only) and negative (nonexistent Rule ID → 0 rows, no error, no fallback) |
| Reranker (`reranker.model = "databricks_reranker"`) | **UNAVAILABLE_IN_CURRENT_ENVIRONMENT** — API schema accepted, but returned: *"The Reranker feature is enabled in this workspace, however a workspace-level configuration is preventing us from accessing the reranker model."* Not an infrastructure change attempted; recorded as a hard capability limit. |

## Experiment Group A — Identifier-only retrieval

Dataset: `rag/evaluation/dataset/identifier_queries_v1.jsonl`, 14 queries
(`GOLD-NNN` only), 4 documents (business_rules ×4, inventory ×4,
forecasting ×3, metrics ×3), including the known failure `GOLD-032`
(= BR-03). Labels derived from the corpus before any of these calls.

| Experiment | query_type | filter | query text | Calls |
|---|---|---|---|---|
| A0 (control) | HYBRID | none | literal (`"GOLD-032"`) | 14 |
| A1 | FULL_TEXT | none | literal | 14 |
| A2 | HYBRID | `rule_ids` exact match, derived from the query text only | literal | 14 |
| A3 | HYBRID | none | `"regra {rule_id}"` template, `rule_id` extracted from the query text only | 14 |

No-oracle-leakage: `src/retrieval/query_routing.py` has zero import of the
evaluation package (mechanically enforced by
`test_module_never_imports_evaluate_retrieval` /
`test_functions_never_reference_evaluate_retrieval_module`); the filter
and expansion in A2/A3 are derived only from `extract_single_rule_id(query)`.

### Results

| Metric | A0 HYBRID | A1 FULL_TEXT | A2 rule_ids filter | A3 expansion |
|---|---|---|---|---|
| Hit@1 | 0.000 | 0.071 | **0.429** | 0.000 |
| Hit@3 | 0.000 | 0.786 | 0.857 | 0.143 |
| Hit@5 | 0.071 | **1.000** | **1.000** | 0.214 |
| Hit@15 | 0.286 | 1.000 | 1.000 | 0.286 |
| MRR | 0.045 | 0.443 | **0.631** | 0.068 |
| Latency mean | 656.9ms | 302.0ms | 505.5ms | 475.7ms |
| Latency p50 | 596.8ms | 246.0ms | 417.7ms | 435.6ms |
| Latency p95 | 892.6ms | 362.9ms | 668.7ms | 585.8ms |

`GOLD-032` (ID-03) rank of correct evidence: A0 = **never found in Top-15**;
A1 = rank 3; A2 = rank 4 (of 5 total matches returned by the filter); A3 =
never found. A2 also returns far fewer candidates overall (the filter
narrows the corpus to only chunks carrying that Rule ID — e.g. 5 for
`GOLD-032`), which is itself a useful property for a deterministic
identifier lookup.

**HYBRID (the current production query_type) is confirmed systemically
weak for bare-identifier queries** — this is not a BR-03 quirk; 10 of 14
identifiers were never found in Top-15 under HYBRID.

## Experiment Group B — Reranker

**UNAVAILABLE_IN_CURRENT_ENVIRONMENT.** No aggregate metrics, no BR-03/FC-05/
cross-source/generic-heading analysis under reranking could be produced —
the feature never returned a result in this workspace. Sections 9–19 of the
task template are answered as "not determinable here," not guessed.

## Negative/OOD diagnostic (HYBRID vs FULL_TEXT, top-1 only)

| Query | HYBRID top-1 (score) | FULL_TEXT top-1 (score) |
|---|---|---|
| NEG-01 (férias) | powerbi > Objetivo (0.942) | powerbi > Objetivo (2.801) |
| NEG-02 (recommendation engine) | inventory > Quantidade sugerida (0.955) | powerbi > Objetivo (2.801) |
| NEG-03 (delivery SLA) | gold_business_rules > Lead time (0.955) | gold_business_rules > Prioridade RFM (3.559) |
| NEG-04 (Databricks password) | powerbi > Proveniência (0.500) | **no results** |

FULL_TEXT scores are on a **different, incomparable scale** (2.8–3.6 vs.
0.5–1.0) — never compare raw scores across `query_type`. FULL_TEXT returned
zero results for NEG-04 (no lexical overlap at all) instead of a
low-score neighbor — arguably a clearer negative signal than HYBRID's
neighbor-of-last-resort behavior, but this is a single observation, not a
validated property. No worse false-positive behavior observed for the
other three negatives; no threshold implemented.

## Call discipline

Capability probes: 9. Identifier experiments: 14×4 = 56. Negative/OOD
diagnostic: 8 (4 HYBRID + 4 FULL_TEXT) — **the 4 HYBRID calls here were
avoidable**, since `baseline_v1_corrected`/`baseline_v1` already contains
HYBRID results for these exact 4 negative queries; they were re-run instead
of reused. Disclosed rather than hidden. Total new calls this task: 73.
No baseline HYBRID reruns for the 37-query v2 set; all Phase 4A.1
comparisons reused existing `raw_results.jsonl`.
