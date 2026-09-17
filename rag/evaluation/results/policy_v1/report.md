# Phase 4C — Retrieval Policy Report

Policy: `rag/src/retrieval/policy.py`. Config: `DEFAULT_POLICY_CONFIG` =
`normal_top_k=8, cross_source_top_k=10` (`EXACT_RULE_ID_TOP_K=5` is a
separate module constant, fixed by Phase 4B evidence). Reference baseline:
Phase 4A.1 (`baseline_v1_corrected/`) + Phase 4B (`identifier_rule_filter_v1/`).

## Offline Top-K policy-boundary analysis (0 new AI Search calls)

Reclassified the 37-query v2 dataset: 1 EXACT_RULE_ID (BR-03), 3
CROSS_SOURCE (CS-01/02/03 — CS-04 is a known, disclosed false negative of
the conservative cue rule and falls through to NORMAL), 29 NORMAL
(including CS-04), 4 negative kept separate.

**NORMAL_SINGLE_SOURCE (29 queries):**

| K | Hit | Coverage | Precision | nDCG | Misses |
|---|---|---|---|---|---|
| 3 | 0.966 | 0.948 | 0.437 | 0.810 | FC-05 |
| 5 | 0.966 | 0.966 | 0.269 | 0.811 | FC-05 |
| 8 | **1.000** | **1.000** | 0.190 | 0.809 | — |
| 10 | 1.000 | 1.000 | 0.152 | 0.809 | — |
| 15 | 1.000 | 1.000 | 0.110 | 0.795 | — |

**CROSS_SOURCE (3 queries):**

| K | Hit | Coverage | Precision | nDCG |
|---|---|---|---|---|
| 3 | 1.000 | 0.667 | 0.667 | 1.000 |
| 5 | 1.000 | 0.667 | 0.533 | 0.950 |
| 8 | 1.000 | 0.833 | 0.375 | 0.928 |
| 10 | 1.000 | **1.000** | 0.367 | 0.890 |
| 15 | 1.000 | 1.000 | 0.244 | 0.890 |

## Selected Top-K

- `normal_top_k = 8` — smallest K with zero known misses (FC-05 resolves
  exactly here); precision keeps dropping past K=8 with no further
  Hit/Coverage gain, and nDCG is flat (0.810→0.809), so K=8 is not a
  max-recall pick, it is the point where marginal K stops buying anything.
- `cross_source_top_k = 10` — smallest K with complete coverage across all
  3 classified cross-source queries; nDCG@10 == nDCG@15 (0.890), so K=15
  adds noise, not signal. **Caveat: only 3 queries feed this number** —
  see Known limitations.
- `EXACT_RULE_ID_TOP_K = 5` — carried over unchanged from Phase 4B
  Experiment A2 (Hit@5=1.000, MRR=0.631).

## Routing precedence

`EXACT_RULE_ID` → `CROSS_SOURCE` → `NORMAL`. Exact-identifier requires the
*entire* query to be one `GOLD-NNN` token. Cross-source requires an
explicit Gold-side cue (`\bgold\b`, excluding a `GOLD-NNN` token via a
negative lookahead) **and** a Power BI-side cue (`power bi`, `dashboard`,
`modelo semântico`, `semantic model`) in the same query. No NLP/LLM
classifier; false negatives (e.g. CS-04) are accepted over invented intent.

## Runtime smoke test (5 live calls, `smoke_test_results.json`)

| Case | Query | Route | top_k | filter | Result |
|---|---|---|---|---|---|
| A | `GOLD-032` | EXACT_RULE_ID | 5 | `rule_ids=GOLD-032` | correct evidence at rank 4/5 |
| B | `GOLD-999` | EXACT_RULE_ID | 5 | `rule_ids=GOLD-999` | **0 results, no fallback** |
| C | "Como o Power BI calcula a medida Ticket Médio?" | NORMAL | 8 | none | correct evidence at rank 1/8 |
| D | "Como o Ticket Médio é definido no Gold e implementado no Power BI?" | CROSS_SOURCE | 10 | none | **both** evidence units present (rank 1 powerbi, rank 9 metrics_dictionary) |
| E | FC-05 original query | NORMAL | 8 | none | correct evidence at rank 6/8 |

All 5 match the offline-predicted behavior exactly.

## Offline policy-level regression (0 new calls; identifier route reuses
Phase 4B A2 raw results, normal/cross-source reuse Phase 4A.1 HYBRID
Top-15 truncated to the route's own top_k)

| Route | n | Hit | Coverage | Precision | MRR | Misses |
|---|---|---|---|---|---|---|
| EXACT_RULE_ID | 1 | 1.000 | 1.000 | 1.000 | 0.250 | — |
| CROSS_SOURCE | 3 | 1.000 | 1.000 | 0.367 | 1.000 | — |
| NORMAL | 29 | 1.000 | 1.000 | 0.190 | 0.759 | — |
| **Overall (33 positive)** | 33 | **1.000** | **1.000** | 0.230 | 0.765 | **0** |

**Zero misses under the policy**, versus 2 in the flat-Top-15 corrected
baseline (BR-03, FC-05) — both resolved by routing, not by raising K
globally. Negative/OOD queries route NORMAL (kept diagnostic-only, not in
the aggregate) — unchanged top-1 scores from Phase 4A.1/4B (no threshold
introduced).

## Known limitations (disclosed, not hidden)

- **Reranker remains UNAVAILABLE_IN_CURRENT_ENVIRONMENT** (workspace
  configuration block, confirmed in Phase 4B) — not attempted again here.
- **Generic-heading pollution is unresolved** — Phase 4A.1 found 26/31
  generic-heading Top-5 occurrences graded irrelevant; this policy changes
  *which* K is used per route, not *what* gets returned within that K, so
  the underlying pollution is unchanged. No Markdown was edited.
- **OOD evidence-sufficiency is unresolved** — no score threshold exists;
  3 of 4 negative queries still score in the same range as genuine
  positives (see Phase 4A.1/4B).
- **cross_source_top_k=10 is derived from only 3 queries.** Directionally
  consistent with the original 4-query CS-01..04 finding (2 of the 3
  classified queries needed K>5, matching CS-01/CS-03's known individual
  behavior), but the sample is small.
- **`is_explicit_cross_source` has a known false negative** (CS-04) by
  design — disclosed, not silently fixed by broadening the cue list.
