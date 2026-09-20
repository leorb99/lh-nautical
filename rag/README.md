# LH Nautical — AI Harness / RAG

Conversational AI backend for the LH Nautical project, implemented on Databricks.

The solution combines two controlled information paths behind a single API:

- **RAG / AI Search** for documentary knowledge, business rules, methodology and explanatory content.
- **Structured Data** for dynamic facts and analytical outputs stored in the Lakehouse, such as revenue, inventory and forecast results.

The AI Harness decides which source is appropriate for the user's question. The LLM does not generate arbitrary SQL and does not freely choose tables.

---

## Current Status

| Component | Status |
|---|---|
| Canonical RAG knowledge base | IMPLEMENTED / RUNTIME VALIDATED |
| RAG corpus | 6 canonical documents / 163 chunks |
| Delta `document_chunks` | IMPLEMENTED / RUNTIME VALIDATED |
| Databricks AI Search | IMPLEMENTED / RUNTIME VALIDATED |
| `document_chunks_index` | READY / 163 indexed chunks |
| Retrieval policy | IMPLEMENTED / RUNTIME VALIDATED |
| LlamaIndex orchestration | IMPLEMENTED / RUNTIME VALIDATED |
| Databricks Llama generation | IMPLEMENTED / RUNTIME VALIDATED |
| AI Harness | IMPLEMENTED |
| `AI_SEARCH` path | LIVE / RUNTIME VALIDATED |
| `STRUCTURED_DATA` path | LIVE / RUNTIME VALIDATED for validated analytical families |
| `BOTH` routing | IMPLEMENTED / UNIT TESTED; no separate end-to-end smoke claim |
| Revenue / sales queries | LIVE VALIDATED |
| Inventory queries | LIVE VALIDATED |
| Product × store P85 forecast | IMPLEMENTED / TESTED; pending final App data access/deployment |
| MLflow request tracing | LIVE / RUNTIME VALIDATED |
| Databricks App `/api/ask` | IMPLEMENTED / LIVE |
| Local RAG test suite | **1500 passed** |
| Production promotion | IN PROGRESS — do not claim production runtime validation until final smoke tests pass |
| Chatbot UI | Separate frontend integration |

The currently validated runtime environment is the deployed Databricks App used during development. Production promotion is a deployment concern and does not change the HTTP contract described below.

---

## Architecture

The solution is not simply "Vector Search + LLM".

It is a hybrid conversational system in which documentary and numerical questions use different controlled access paths.

```text
                         USER / CHAT UI
                              |
                              v
                        POST /api/ask
                              |
                              v
                     +------------------+
                     |    AI HARNESS    |
                     |------------------|
                     | validation       |
                     | source routing   |
                     | guardrails       |
                     | failure handling |
                     +--------+---------+
                              |
               +--------------+--------------+
               |                             |
               v                             v
       DOCUMENTARY PATH               STRUCTURED PATH
               |                             |
       Retrieval Policy               Semantic Registry
               |                             |
       Databricks AI Search           AnalyticalRequest
               |                             |
        document_chunks               Validated Compiler
               |                             |
          LlamaIndex                    Delta / SQL
               |                             |
        Databricks Llama              Gold / ML outputs
               |                             |
               +--------------+--------------+
                              |
                              v
                      NATURAL-LANGUAGE
                         RESPONSE
                              |
                              v
                         Databricks App

                 QUALITY / OBSERVABILITY
                          MLflow
```

### Six logical layers

1. **Knowledge / Data**
   - canonical Markdown;
   - Delta / Gold structured data;
   - analytical and ML outputs.

2. **Retrieval**
   - Databricks AI Search;
   - Hybrid Search;
   - deterministic retrieval policy.

3. **Generation**
   - Databricks-hosted Llama.

4. **Orchestration**
   - LlamaIndex for the RAG execution path.

5. **AI Harness / Quality**
   - request validation;
   - source routing;
   - structured-query control;
   - fail-closed behavior;
   - citation integrity;
   - tracing.

6. **Interface**
   - Databricks App;
   - stable `/api/ask` contract.

MLflow is transversal to the architecture and provides request tracing and operational observability.

---

## Source Routing

The user interacts with one conversational endpoint, but the backend does not answer every question through RAG.

```text
Question
   |
   v
AI Harness
   |
   +--> AI_SEARCH
   |      documentary knowledge
   |
   +--> STRUCTURED_DATA
   |      numerical / analytical facts
   |
   +--> BOTH
          supported mixed-source intents
```

Routing is deterministic and controlled by application code. It is not delegated to an unrestricted LLM classifier.

### Typical source ownership

| Question type | Primary source |
|---|---|
| Business rules / policies | RAG / AI Search |
| Methodology / documented definitions | RAG / AI Search |
| Revenue / sales | Structured Data |
| Inventory | Structured Data |
| Historical analytical facts | Structured Data |
| Product × store P85 forecast | Structured Data |
| Documentary explanation of model methodology | RAG |
| Unsupported or insufficient evidence | Fail closed |

The same business question must not silently switch to an unrelated source just because the preferred source has no result.

---

# RAG Path

## Canonical Knowledge Base

The current authorized corpus contains six curated Markdown documents:

| File | Domain |
|---|---|
| `gold_business_rules.md` | Business rules |
| `inventory_and_stockout.md` | Inventory / stockout |
| `demand_forecasting.md` | Demand forecasting |
| `metrics_dictionary.md` | Metrics |
| `gold_data_architecture.md` | Data architecture |
| `powerbi_semantic_rules.md` | Power BI semantics |

Five documents were curated from Gold/dbt/SQL analysis.

`powerbi_semantic_rules.md` was curated from direct inspection of the Power BI PBIP/TMDL semantic model.

Raw PDFs, PBIX files and PBIP/TMDL files are not directly indexed by the current RAG pipeline.

Additional analytical reports must only be described here as part of the RAG corpus after they have actually been ingested and retrieval-validated.

---

## Ingestion

```text
Canonical Markdown
       |
       v
    loader.py
       |
       v
    parser.py
       |
       v
    chunker.py
       |
       v
    Chunk[]
       |
       v
persist_chunks()
       |
       v
Delta document_chunks
       |
       v
Databricks AI Search
```

### Current corpus metrics

| Metric | Value |
|---|---:|
| Canonical documents | 6 |
| Parsed sections | 186 |
| Heading-only sections skipped | 23 |
| Final chunks | 163 |
| Sections requiring internal split | 0 |
| Maximum estimated tokens in one chunk | 642 |

### Chunks by document

| Document | Chunks |
|---|---:|
| `gold_business_rules` | 17 |
| `inventory_and_stockout` | 19 |
| `demand_forecasting` | 12 |
| `metrics_dictionary` | 29 |
| `gold_data_architecture` | 22 |
| `powerbi_semantic_rules` | 64 |
| **Total** | **163** |

---

## Persistence

`document_chunks` is a Delta table with deterministic chunk identity and Change Data Feed enabled.

Chunk IDs are deterministic:

```text
chunk_id = SHA-256(
    document_id
    + section_path
    + chunk_index
    + content_hash
)
```

The persistence layer uses a scoped Delta `MERGE`.

Important properties:

- identical re-ingestion does not rewrite unchanged chunks;
- changed content receives a new chunk identity;
- stale chunks are deleted only for documents included in the current processing scope;
- an unexpectedly missing source document is not automatically interpreted as an intentional retirement.

Runtime validation demonstrated:

```text
Initial 5-document corpus       -> 99 chunks
Identical rerun                 -> 0 changes
Sixth document added            -> +64 chunks
Final corpus                    -> 163 chunks
```

---

## Embeddings and AI Search

Embedding endpoint:

```text
databricks-qwen3-embedding-0-6b
```

Vector Search endpoint:

```text
rag_endpoint
```

Index:

```text
document_chunks_index
```

Configuration:

```text
Delta Sync: TRIGGERED
Retrieval:  HYBRID
Primary key: chunk_id
Text column: chunk_text
```

The current validated index contains 163 indexed rows.

---

## Retrieval Policy

Retrieval policy is deterministic and separate from AI Harness source routing.

```text
EXACT_RULE_ID
    -> Hybrid Search
    -> strong rule_id filtering
    -> Top-K = 5

NORMAL
    -> Hybrid Search
    -> Top-K = 8

CROSS_SOURCE
    -> Hybrid Search
    -> Top-K = 10
```

This policy was selected from retrieval experiments and runtime validation.

The Databricks reranker is not part of the current policy.

---

## Generation

RAG generation uses:

```text
databricks-meta-llama-3-3-70b-instruct
```

The RAG orchestration path is:

```text
AI Search evidence
      |
      v
LlamaIndex adapter
      |
      v
deterministic source labels
      |
      v
grounding context
      |
      v
Databricks Llama
      |
      v
answer + source metadata
```

LlamaIndex orchestrates the RAG execution path. It does not redefine retrieval policy or choose arbitrary Structured Data queries.

---

# Structured Data

Structured Data exists for questions whose answers must come from current Lakehouse facts rather than documentary chunks.

Examples:

```text
"Qual foi o faturamento em janeiro de 2026?"

"Qual é o estoque atual de coletes?"

"Qual a previsão P85 do Produto X na Loja Y nos próximos 3 meses?"
```

The LLM does not write arbitrary SQL.

The execution flow is:

```text
Natural-language question
        |
        v
controlled parser
        |
        v
AnalyticalRequest
        |
        v
semantic registry
        |
        v
compatibility validation
        |
        v
deterministic SQL compiler
        |
        v
SQL Warehouse / Gold
        |
        v
deterministic renderer
        |
        v
natural-language response
```

Catalog, schema, table and approved columns come from the semantic registry, never from user input.

Filters are parameterized.

Unsupported combinations fail closed.

---

## Semantic Model

The Structured Data layer uses an explicit semantic registry containing:

- approved facts;
- metrics;
- dimensions;
- allowed operations;
- filters;
- source tables;
- compatibility rules;
- rendering metadata;
- source-specific constraints.

The current registry includes **16 metrics and 10 dimensions**, including the product × store P85 forecast capability.

Structured numerical values are rendered deterministically by application code. They are not passed to an LLM for free-form arithmetic or reinterpretation.

---

# Product × Store Forecast P85

## Purpose

`DEMAND_FORECAST_P85` exposes the available product × store ML forecast safely through the Structured Data path.

The source is:

```text
demand_forecast_rolling_3m
```

The table/catalog location is controlled by the semantic registry.

The user cannot select an arbitrary catalog, schema or table.

---

## Semantics

The available value is a **P85 quantile forecast — the 85th percentile**.

It must not be described as:

```text
expected demand
mean demand
average expected demand
```

User-facing language should identify it as:

> previsão quantílica P85 (percentil 85)

with the clarification:

> É um cenário mais conservador para planejamento de estoque e não representa a demanda média esperada.

The current application contract does not claim that P85 represents a business-defined 85% service level.

---

## Eligibility Gate

A product × store forecast is returned only when the series satisfies all conditions below:

```text
ELIGIBLE_PRODUCT_STORE_FORECAST =

    model_version == "3"

    AND exactly 3 rows exist
        for the official 3-month window

    AND exactly 3 distinct months exist

    AND all 3 rows belong to version 3

    AND generated_at is consistent
        across all 3 rows

    AND all 3 forecast values are non-null

    AND the question resolves to exactly
        one product × one store
```

The official horizon is derived from the model output:

```text
window_end = MAX(month_start)
             WHERE model_version = "3"

window_start = window_end - 2 months
```

This avoids using the wall clock or maintaining a hard-coded month list.

---

## Fail-Closed Behavior

The system does **not**:

- fall back to v1;
- mix model versions;
- silently return incomplete horizons;
- interpret missing months as zero;
- aggregate multiple products;
- aggregate multiple stores;
- rank products by P85;
- sum P85 across the quarter as though quantiles were additive.

When complete P85 coverage is unavailable, the response is:

> Não há previsão P85 disponível para esse produto nessa loja em todos os três meses solicitados.

This gate validates **coverage and output integrity**.

It does **not** certify the statistical quality of the underlying model.

Model-quality evaluation remains a separate Data Science concern.

---

# API

The backend exposes a single primary conversational endpoint:

```http
POST /api/ask
Content-Type: application/json
Authorization: Bearer <token>
```

Request:

```json
{
  "query": "Qual é o estoque atual de coletes?"
}
```

The public response contract remains stable across RAG and Structured Data routes.

Primary response fields:

```text
status
answer
sources
citation_integrity
message
```

Internal source routing is intentionally not exposed as a different HTTP API.

---

## Public Status Behavior

Examples of public statuses include:

```text
AI_SEARCH_OK
AI_SEARCH_NO_EVIDENCE
INVALID_REQUEST
```

The public status contract was kept stable while Structured Data was added.

A structured answer may therefore preserve the existing public success status even though its internal source was Structured Data.

---

# Databricks App

The FastAPI backend is deployed as a Databricks App.

The App owns:

- request handling;
- Harness invocation;
- SQL execution through the configured warehouse;
- RAG calls;
- model calls;
- tracing;
- response serialization.

The frontend does **not** need direct SQL Warehouse access.

The frontend only needs permission to use the App and calls the `/api/ask` contract.

---

## Environment Configuration

Important runtime configuration includes:

```text
RAG_CATALOG
DATA_CATALOG
DATA_SCHEMA
SQL_WAREHOUSE_ID
MLFLOW_TRACKING_URI
MLFLOW_EXPERIMENT_ID
```

The application code is environment-independent where possible.

Catalog selection is supplied through configuration / registry metadata rather than user-provided SQL identifiers.

---

# Observability

MLflow request tracing is implemented and runtime validated.

One logical request is wrapped by a parent Harness trace, with child spans from retrieval and generation where applicable.

Current observability covers:

- request inputs;
- outputs;
- execution path;
- token usage;
- model cost metadata where available;
- latency / span structure.

A systematic MLflow evaluation harness for automated answer-quality regression remains future hardening work.

---

# Testing

Latest complete local RAG test suite:

```text
1500 passed
```

This includes coverage for:

- ingestion;
- parsing;
- chunking;
- persistence contracts;
- retrieval policy;
- orchestration;
- generation adapters;
- Harness behavior;
- tracing;
- citations;
- Structured Data parser;
- semantic registry;
- compiler;
- rendering;
- analytical compatibility;
- inventory analytics;
- product × store P85 forecast;
- App behavior;
- fail-closed paths.

The P85 feature contains dedicated tests for:

- valid three-month v3 series;
- v1-only series;
- mixed v1/v3 series;
- missing months;
- displaced horizons;
- inconsistent `generated_at`;
- null forecast values;
- ambiguous product matching;
- ambiguous store matching;
- missing product/store;
- unsupported horizons;
- parser behavior;
- SQL parameterization;
- deterministic rendering;
- fail-closed App response.

Runtime tests and local unit tests are intentionally distinct.

A local test passing does not automatically imply production runtime validation.

---

# Validated Runtime Behaviors

The deployed App has been runtime validated for representative cases including:

### RAG

```text
valid Rule ID
unknown Rule ID
invalid / whitespace request
```

### Structured Data

Representative live checks include:

```text
historical revenue / sales
current inventory
inventory by product
```

Structured Data families not explicitly smoke-tested must not be described as runtime validated solely because they are present in the registry.

`BOTH` is implemented and unit tested but is not separately claimed here as end-to-end runtime validated.

---

# Production Promotion

The application is designed so the validated development architecture can be reproduced in the production catalog.

Production promotion requires:

```text
1. production rag schema
2. document_chunks table
3. ingestion of canonical documents
4. document_chunks_index creation
5. index synchronization
6. App service-principal permissions
7. production App deployment/binding
8. end-to-end smoke tests
```

The production RAG should reproduce the validated six-document corpus:

```text
6 documents
163 chunks
```

provided the canonical files have not changed.

Do not mark production as runtime validated until these steps have completed successfully.

---

## Required App Permissions

The App's service principal requires only the minimum privileges necessary for its runtime sources.

Conceptually:

```text
USE CATALOG
USE SCHEMA
SELECT on approved Structured Data tables
USE SCHEMA on rag
SELECT on the RAG index / required objects
CAN_USE on SQL Warehouse
CAN_QUERY on LLM endpoint
required MLflow experiment access
```

Prefer object-level `SELECT` grants rather than broad schema-wide data access where practical.

---

## Deployment Flow

Repository promotion follows the project Git workflow.

```text
feature branch
      |
      v
develop
      |
      v
main
      |
      v
bundle deploy -t prod
```

Production deployment is triggered from `main`.

Do not merge to `main` until the production prerequisites and permissions are ready.

A successful bundle validation is not equivalent to a successful runtime smoke test.

---

# Known Limitations

The following remain explicit limitations or future hardening work:

- production promotion must be runtime validated before being called complete;
- P85 output is not expected/mean demand;
- P85 eligibility validates coverage/integrity, not predictive accuracy;
- product × store forecast is intentionally limited to one product × one store × three official months;
- no fallback to previous forecast versions;
- no P85 aggregation/ranking;
- some Structured Data families are implemented but have not received individual live smoke validation;
- the `BOTH` path is not separately claimed as runtime smoke validated;
- Databricks reranking is unavailable in the current environment and is not part of the retrieval policy;
- generic-heading retrieval pollution remains a known retrieval-quality concern;
- broader OOD/evidence-sufficiency judgment remains limited;
- context/token-budget optimization is not implemented;
- MLflow systematic evaluation harness is not implemented;
- external PDF/DOCX/PPTX ingestion is outside the current canonical corpus;
- chatbot UI is maintained separately from this backend;
- business-role literals such as `'Salesperson'` must not be treated as validated unless confirmed against the source data.

---

# CP5 / Analytical Integration

The final conversational solution is intended to expose analytical results through the source that best matches their semantics.

```text
Realized sales / revenue
    -> Structured Data

Inventory
    -> Structured Data

Product × store P85 forecast
    -> Structured Data

Model methodology / documented interpretation
    -> RAG

Seasonality analysis
    -> RAG once the final report is ingested and validated

Distribution-center / facility-location recommendation
    -> RAG once the final report is ingested and validated

Store-level forecast / growth
    -> Structured Data when the approved analytical source is registered
       and runtime validated
```

No capability should be documented as live merely because a report or table exists.

The Harness should expose it only after its source contract and runtime behavior are validated.

---

# Security and Safety Principles

The implementation follows these rules:

```text
No arbitrary LLM-generated SQL.

No user-provided table/schema/catalog identifiers.

No silent fallback to weaker data.

No mixing model versions.

No fabricated numerical answers when structured evidence is absent.

No unsupported aggregation of quantile forecasts.

No automatic broadening of Unity Catalog privileges.

Unsupported or ambiguous requests fail closed.
```

---

# Repository Structure

High-level structure:

```text
rag/
├── app.py
├── app.yaml
├── knowledge_base/
├── sql/
├── src/
│   ├── evaluation/
│   ├── generation/
│   ├── harness/
│   ├── ingestion/
│   ├── orchestration/
│   ├── retrieval/
│   └── structured/
└── tests/
```

Important Bundle resource:

```text
resources/rag_app.yml
```

---

# Handover

For operational handover, maintain the following separation:

### Stable application contract

```text
POST /api/ask
```

The frontend should depend on this API rather than on internal Lakehouse or model details.

### RAG knowledge

Documentary information must be curated, ingested and retrieval-validated before being presented as available knowledge.

### Structured facts

Dynamic numerical values must come from approved Structured Data facts and metrics.

### ML outputs

ML outputs must be materialized in a controlled analytical source before the Harness can consume them.

The LLM is never the source of truth for a numerical prediction.

---

# Definition of Done

The backend is considered technically complete for the current scope when:

- the canonical knowledge corpus is persisted and searchable;
- retrieval policy is validated;
- generation is grounded in retrieved evidence;
- Structured Data uses controlled semantic contracts;
- unsupported queries fail closed;
- the App exposes the stable `/api/ask` contract;
- MLflow tracing is active;
- the local suite remains green;
- production data permissions follow least privilege;
- production RAG is synchronized;
- production App smoke tests pass;
- frontend integration succeeds without requiring direct data-layer access.

Until the production promotion and smoke tests are completed, the correct status is:

```text
IMPLEMENTED / VALIDATED IN CURRENT RUNTIME
PRODUCTION PROMOTION IN PROGRESS
```

After successful production deployment and final smoke tests, this status may be changed to:

```text
PRODUCTION DEPLOYED / RUNTIME VALIDATED
```
