# Phase 4A.1 — Evidence-Level Baseline Correction Report

Same retrieval results as Phase 4A (`baseline_v1/raw_results.jsonl`, 37
queries × Top-15, zero new AI Search calls). Re-scored against
`retrieval_queries_v2.jsonl` (`expected_evidence`, not
`expected_document_ids`) using the corrected matching rules in
`evaluate_retrieval.py`. `baseline_v1/` is preserved untouched.

## Root cause

`hit_at_k` and `evidence_coverage_at_k` checked only whether the
*expected document* appeared in Top-K, ignoring which section. A
same-document, wrong-section chunk counted as full success. Confirmed
concretely: query `BR-03` = `"GOLD-032"` never retrieved the actual
GOLD-032 chunk anywhere in Top-15; an unrelated "Objetivo" chunk from the
same document at rank 11 made the old `Hit@15 = True`. Document match is
not evidence match.

## Corrected metrics by K

| K | Hit@K | Evidence Coverage@K | Precision@K | nDCG@K |
|---|---|---|---|---|
| 3 | 0.939 | 0.894 | 0.455 | 0.822 |
| 5 | 0.939 | 0.909 | 0.297 | 0.819 |
| 8 | 0.970 | 0.955 | 0.212 | 0.815 |
| 10 | 0.970 | 0.970 | 0.176 | 0.811 |
| 15 | 0.970 | 0.970 | 0.125 | 0.799 |

Corrected MRR: **0.758**.

## Original (document-level) vs corrected — delta

| Metric | K | Original | Corrected | Delta |
|---|---|---|---|---|
| Hit@K | 3 | 0.970 | 0.939 | -0.030 |
| Hit@K | 5 | 0.970 | 0.939 | -0.030 |
| Hit@K | 8 | 0.970 | 0.970 | 0.000 |
| Hit@K | 10 | 0.970 | 0.970 | 0.000 |
| Hit@K | 15 | **1.000** | **0.970** | **-0.030** |
| Evidence Coverage@K | 3 | 0.924 | 0.894 | -0.030 |
| Evidence Coverage@K | 5 | 0.939 | 0.909 | -0.030 |
| Evidence Coverage@K | 15 | 1.000 | 0.970 | -0.030 |
| Precision@K | 3 | 0.707 | 0.455 | **-0.253** |
| Precision@K | 5 | 0.667 | 0.297 | **-0.370** |
| Precision@K | 15 | 0.533 | 0.125 | **-0.409** |
| MRR | — | 0.897 | 0.758 | **-0.139** |

Hit@15 = 1.000 in the original methodology was itself a false signal —
under the corrected definition it is 0.970: BR-03's evidence genuinely
never appears, at any K. Precision is the most inflated metric by far
(document-level "grade ≥1 for any same-document chunk" credited most of
Top-15 as relevant).

## Other false successes found

Systematic scan (document appears in Top-K but no expected evidence is
satisfied), beyond the already-known BR-03:

| query_id | query | document | wrong retrieved section (in old Top-K) | expected evidence | rank of correct evidence |
|---|---|---|---|---|---|
| BR-03 | "GOLD-032" | gold_business_rules | Objetivo (rank 11) | Definição de pedido de compra em aberto / GOLD-032 | **never found in Top-15** |
| FC-05 | O que acontece quando um mês não teve nenhuma venda... | demand_forecasting | Série de demanda (r2), Construção do histórico mensal (r3), Estado atual do pipeline (r4), Objetivo (r5) | Tratamento de meses sem demanda | rank 6 (found, just outside old Top-5) |

BR-03 is a genuine, permanent miss at every K. FC-05 was a false success
specifically at K=5 (old Hit@5 = True via document match; corrected
Hit@5 = False, Hit@8 = True).

## Metrics by category (MRR / Hit@5)

| Category | Original MRR | Corrected MRR | Original Hit@5 | Corrected Hit@5 |
|---|---|---|---|---|
| business_rules | 0.818 | 0.800 | 0.80 | 0.80 |
| inventory | 0.900 | 0.900 | 1.00 | 1.00 |
| forecasting | 0.800 | **0.400** | 1.00 | **0.80** |
| metrics | 0.900 | 0.900 | 1.00 | 1.00 |
| data_architecture | 1.000 | **0.667** | 1.00 | 1.00 |
| powerbi | 1.000 | **0.767** | 1.00 | 1.00 |
| cross_source | 0.875 | 0.875 | 1.00 | 1.00 |

`forecasting`, `data_architecture`, and `powerbi` were the categories
most inflated by document-level matching — their generic "Objetivo" /
front-matter sections previously counted toward MRR.

## Cross-source evidence coverage (evidence-level, unchanged in outcome)

| Query | K=3 | K=5 | K=8 | K=10 | K=15 |
|---|---|---|---|---|---|
| CS-01 (Ticket Médio) | 0.5 | 0.5 | 0.5 | 1.0 | 1.0 |
| CS-02 (Pedidos Abertos) | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| CS-03 (no_recent_sales) | 0.5 | 0.5 | 1.0 | 1.0 | 1.0 |
| CS-04 (RFM) | 0.5 | 1.0 | 1.0 | 1.0 | 1.0 |

Numerically identical to Phase 4A for these 4 queries: each evidence unit
already required a real rule_id/section-cue match by design in the
original cross-source labels, so this category was not where the bug
lived — it is now proven correct rather than assumed correct.

## Generic-heading analysis (corrected)

31 generic-heading hits in Top-5 (unchanged count). Grade distribution
changed drastically: **26 of 31 (84%) are now grade 0**, versus only 5 of
31 (16%) under the old methodology. Only 5 remain grade ≥1, all
legitimate cross-references via a shared Rule ID appearing in a
"Relação com..." closing section (BR-03 ×2, CS-01, CS-02, CS-03) — not
generic "Objetivo" sections, which are now uniformly grade 0.

## Negative/OOD

Unchanged from Phase 4A (negative queries were never subject to the
document-level bug — `grade_relevance` already forced 0 for them).
Positive top-1 scores: 0.969–1.0. Three of four negative top-1 scores:
0.942–0.955 (NEG-04: 0.500). No threshold proposed.

## Reranking classification

# STRONG_EVIDENCE_TO_TEST

Justification, using corrected metrics: Hit@K and Evidence Coverage@K
remain high (0.94–0.97) — the right evidence is present in Top-15 for
almost every query — while Precision@K collapses to **0.125 at K=15**
(only ~2 of 15 slots are actually useful on average) and MRR drops to
0.758. This is the textbook reranking-opportunity signature: recall is
fine, ranking quality is not. This upgrades the Phase 4A classification
(`WORTH_TESTING`) now that the earlier, inflated precision numbers no
longer mask the gap. Caveat: reranking cannot fix BR-03 (the correct
chunk is absent from the candidate set entirely, at any K) — that
remains a query-formulation/retrieval problem, not a ranking problem.
