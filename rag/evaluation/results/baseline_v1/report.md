# Phase 4A — Retrieval Quality Baseline Report

Dataset: `rag/evaluation/dataset/retrieval_queries_v1.jsonl` (37 queries)
Index: `dev_natalia_correia_lh_nautical.rag.document_chunks_index` (163/163 rows, `ready=true`)
Raw results: `raw_results.jsonl`. Machine-readable summary: `metrics_summary.json`.

This is a measurement baseline. No system change was made based on these
results — see `rag/README.md` Phase 4 and the accompanying task report for
the full narrative and candidate-experiment list for Phase 4B.

## Aggregate metrics by K

| K | Hit rate | Mean evidence coverage | Mean precision | Mean nDCG |
|---|---|---|---|---|
| 3 | 0.970 | 0.924 | 0.707 | 0.879 |
| 5 | 0.970 | 0.939 | 0.667 | 0.869 |
| 8 | 0.970 | 0.955 | 0.598 | 0.861 |
| 10 | 0.970 | 0.970 | 0.579 | 0.858 |
| 15 | 1.000 | 1.000 | 0.533 | 0.853 |

Overall MRR (33 non-negative queries): **0.897**.

## By category (MRR / hit@5 / evidence coverage@5)

| Category | n | MRR | Hit@5 | Coverage@5 |
|---|---|---|---|---|
| business_rules | 5 | 0.818 | 0.80 | 0.80 |
| inventory | 5 | 0.900 | 1.00 | 1.00 |
| forecasting | 5 | 0.800 | 1.00 | 1.00 |
| metrics | 5 | 0.900 | 1.00 | 1.00 |
| data_architecture | 4 | 1.000 | 1.00 | 1.00 |
| powerbi | 5 | 1.000 | 1.00 | 1.00 |
| cross_source | 4 | 0.875 | 1.00 | 0.75 |
| negative | 4 | — (no expected evidence; see score analysis) | — | — |

## Cross-source evidence coverage progression

| Query | K=3 | K=5 | K=8 | K=10 | K=15 |
|---|---|---|---|---|---|
| CS-01 (Ticket Médio Gold + Power BI) | 0.5 | 0.5 | 0.5 | **1.0** | 1.0 |
| CS-02 (Pedidos Abertos Gold + Power BI) | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| CS-03 (no_recent_sales Gold + Power BI) | 0.5 | 0.5 | **1.0** | 1.0 | 1.0 |
| CS-04 (RFM metrics + Power BI) | 0.5 | **1.0** | 1.0 | 1.0 | 1.0 |

CS-01 needed K=10, CS-03 needed K=8. The earlier smoke-test observation
(Power BI dominating Top-8, `metrics_dictionary > average_ticket_amount`
surfacing at rank 9) reproduces almost exactly here (rank 9 confirmed in
raw results). It is the worst cross-source case measured, but not
isolated — half of the 4 cross-source queries needed K>5 for full coverage.

## Negative/OOD score behavior

| Query | Top-1 score | Top-1 result |
|---|---|---|
| NEG-01 (férias/HR) | 0.942 | powerbi_semantic_rules > Objetivo |
| NEG-02 (recommendation engine) | 0.955 | inventory_and_stockout > Quantidade sugerida |
| NEG-03 (customer delivery SLA) | 0.955 | gold_business_rules > Lead time do fornecedor |
| NEG-04 (Databricks password reset) | **0.500** | powerbi_semantic_rules > Proveniência... |

Positive-query top-1 scores cluster tightly at 0.97–1.0 (min observed:
0.969). Three of four negative queries scored 0.94–0.96 — inside noise
distance of genuine positive results — while only NEG-04 showed a large,
unambiguous drop. **Scores do not reliably separate answerable from
unanswerable queries** in this baseline; no threshold is proposed.

## Generic-heading observation

31 of a possible 185 top-5 slots (37 queries × 5) were a generic heading —
**16.8%**. Most (grade ≥ 1) still belonged to the expected document
(harmless redundancy); a few (BR-03, DA-01, DA-02, FC-04, CS-02) were
grade 0 (genuinely irrelevant fill). `powerbi_semantic_rules`'s own
"Objetivo" and "Proveniência e Escopo dos Artefatos" sections appeared in
top-5 for 8 of the 10 powerbi/cross_source queries — a document-specific
pattern, not spread evenly across the corpus.

## Latency (retrieval call only, 37 queries)

count=37, mean=489.7ms, p50=409.2ms, p95=823.3ms, max=1173.4ms. No LLM
call involved.

## Worst single failure: BR-03 ("GOLD-032" bare identifier)

The bare Rule-ID query `"GOLD-032"` never retrieved the actual GOLD-032
section (`gold_business_rules > Definição de pedido de compra em aberto`)
within Top-15. The expected document appeared only at rank 11, via its
unrelated "Objetivo" section (grade 1, not the real evidence) — so
`hit@15=True` is a false signal of success here; evidence_coverage (which
also only checks document_id) shares this blind spot. This is the
clearest, best-evidenced weak case in the baseline.
