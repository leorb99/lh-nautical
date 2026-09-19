# RAG — Grupo 9

RAG (Retrieval-Augmented Generation) pipeline over the canonical Gold-layer
documentation of the LH Nautical project. Goal: grounded Q&A with citations
back to Rule IDs and source sections, over a small, hand-curated Markdown
knowledge base — evolving toward a broader, multi-source document platform.

> **Status note:** this document contains both **implemented** architecture
> and explicitly marked **target/future** architecture. Sections and bullets
> marked PLANNED, DEFERRED, or OPEN DECISION describe agreed design
> direction, not functionality currently available. Only sections/rows
> marked IMPLEMENTED, FINAL / CLOSED, VALIDATED LOCALLY, or RUNTIME
> VALIDATED reflect code and behavior that exist and have been exercised
> today — RUNTIME VALIDATED specifically means exercised against real
> Databricks (Spark/Delta/Unity Catalog/AI Search), not merely locally.
> Nothing in this document should be read as claiming Databricks runtime
> validation has occurred unless explicitly stated as such.

## Status

| Component | Status |
|---|---|
| Knowledge Base (6 canonical documents) | FINAL / CLOSED (current corpus) |
| Ingestion Contract | FINAL / CLOSED |
| `loader.py` | FINAL / CLOSED |
| `parser.py` | FINAL / CLOSED |
| `chunker.py` | FINAL / CLOSED |
| Delta Persistence Design | FINAL / CLOSED |
| Persistence pure-Python components (`schema_contract.py`, `persistence_validation.py`, `persistence_expressions.py`) | IMPLEMENTED, VALIDATED LOCALLY |
| `run_ingestion.py` (Databricks `spark_python_task` entrypoint) | IMPLEMENTED / RUNTIME VALIDATED |
| Spark/Delta runtime components (`spark_schema.py`, `persistence.py`) | IMPLEMENTED / RUNTIME VALIDATED |
| `document_chunks` table | 163 rows / 163 distinct chunk IDs / RUNTIME VALIDATED |
| Delta `MERGE` (first run, identical rerun, incremental 6th-document addition) | RUNTIME VALIDATED |
| Change Data Feed (runtime behavior) | RUNTIME VALIDATED |
| Incremental addition behavior (new canonical document, old chunks untouched) | RUNTIME VALIDATED |
| Power BI semantic rules (`powerbi_semantic_rules.md`) | FINAL / CLOSED / INGESTED / RUNTIME VALIDATED |
| Qwen3-Embedding-0.6B (managed embedding) | SELECTED / AI SEARCH VALIDATED |
| Databricks AI Search (own index on shared `rag_endpoint`) | IMPLEMENTED / RUNTIME VALIDATED (Phase 3A) |
| `document_chunks_index` | READY / 163 indexed rows |
| Hybrid Search | RUNTIME SMOKE/REGRESSION VALIDATED (old + new domains) |
| AI Search Infrastructure as Code (Asset Bundle) | `document_chunks_index` itself: PLANNED / NON-BLOCKING HARDENING. The Databricks App and its MLflow tracing experiment: IMPLEMENTED as Bundle resources (see Phase 8). |
| Corpus Completion Checkpoint (Power BI promotion) | PASS / CLOSED |
| Retrieval Layer | IMPLEMENTED / RUNTIME VALIDATED |
| Retrieval Quality Baseline (Phase 4A, corrected in 4A.1) | IMPLEMENTED / VALIDATED |
| Evidence-Level Evaluator (`evaluate_retrieval.py`) | IMPLEMENTED |
| Identifier Retrieval Experiments (Phase 4B) | COMPLETED |
| Retrieval Policy — routing (Phase 4C) | IMPLEMENTED / RUNTIME VALIDATED |
| **Retrieval Quality & Policy (Phase 4, overall)** | **PASS / CLOSED FOR CURRENT SCOPE** |
| Databricks Reranker | UNAVAILABLE IN CURRENT ENVIRONMENT / NOT PART OF CURRENT POLICY |
| Generic-heading retrieval pollution | OPEN |
| OOD / negative-query evidence sufficiency | OPEN / FUTURE HARNESS CONCERN |
| Structured ML/analytical data path (Delta/SQL for dynamic outputs) | **PARTIALLY IMPLEMENTED (Phase 6B.1, local only)** — generic Structured Analytics over the Gold layer (approved metric + operation + dimension + filters, from the Power BI semantic model); see the next rows and "Structured Data & Source Routing". ML / forecast outputs remain ARCHITECTURALLY DECIDED / DEFERRED UNTIL REAL DATA CONTRACT |
| **LlamaIndex Orchestration** (`orchestration/rag_pipeline.py`, Phase 5A) | **IMPLEMENTED / RUNTIME VALIDATED** |
| **Databricks Llama Generation** (`generation/llama_client.py`, Phase 5A) | **IMPLEMENTED / RUNTIME VALIDATED** |
| **Lean RAG Core** (orchestration consolidation review) | **REFACTORED / VALIDATED** |
| **AI Harness** (`harness/harness.py`, Phase 6A + 6B.1) | **IMPLEMENTED** — request validation, controlled-failure containment, and (Phase 6B.1) `AI_SEARCH` / `STRUCTURED_DATA` / `BOTH` source routing through a deterministic rule-based router (`harness/source_router.py`, no LLM classifier). The structured routes are **implemented and unit-tested locally, NOT LIVE** (see the next row); no context/token-budget selection (see "Top-K vs Token Budget", still open) |
| **Structured Data & Source Routing / Structured Analytics** (`structured/*`, `harness/source_router.py`, Phase 6B.1 and its generalization) | **IMPLEMENTED LOCALLY / UNIT-TESTED / NOT LIVE** — a typed `AnalyticalRequest` (15 approved metrics from the Power BI semantic model, 9 dimensions, aggregate/ranking/breakdown/trend) is compiled by a deterministic SQL compiler and run through an injectable SQL executor; values are **rendered deterministically by code, never by the model**, and unsupported combinations fail closed. Never executed against a real SQL Warehouse from the App. Live validation, the App's `sql_warehouse` resource and its Unity Catalog grants are **Phase 6B.2** — see "Structured Data & Source Routing" |
| Citation Integrity Validation (`evaluation/citations.py`) | IMPLEMENTED — structural check only (cited `[Sn]` labels vs. real source labels); not a semantic groundedness judgment |
| MLflow Request Tracing (`harness/tracing.py`, Phase 7) | **IMPLEMENTED / LIVE RUNTIME VALIDATED** — real live App requests produced real MLflow trace records (parent `AIHarness.ask`, correct inputs/outputs, `mlflow.user` matching the App's own service principal, real token usage/cost, span counts consistent with each request's code path). Individual child-span artifact retrieval was not inspectable with the tooling used (see Roadmap Phase 8) — a non-blocking observability limitation, not a functional gap. MLflow **evaluation harness** (systematic quality regression) remains PLANNED |
| Interface — Databricks App (`app.py` + `resources/rag_app.yml`, Phase 8) | **IMPLEMENTED / DEPLOYED / LIVE VALIDATED** — App is `RUNNING`/`ACTIVE`. All three V1 API-contract paths validated against the real deployed App: `GOLD-032` → `AI_SEARCH_OK`; `GOLD-999` → `AI_SEARCH_NO_EVIDENCE`; whitespace query → `INVALID_REQUEST`/HTTP 400 (see Roadmap Phase 8). This is backend/API validation only — the chatbot UI is separate, in-progress work (see "Interface Layer — Databricks App") |
| External document pipeline (PDF/DOCX/etc.) | PLANNED, DEFERRED until after core RAG |

**Repository / branch state:**

- `rag/` is versioned in Git (previously untracked during earlier
  development; now committed).
- Branch `feature/rag` (Phases 1–5A, through the Lean RAG Core review) was
  merged via **PR #108** (commit `c6e1112`) — this work is on `main` now.
- Current work (AI Harness, MLflow tracing, citation integrity, the
  Databricks App, and the Bundle `resources/rag_app.yml`) is on a
  **separate, newer branch, `feature/ai-harness`**, built on top of the
  merged `feature/rag` work. As of this revision that work is **local and
  uncommitted** — no commit, push, or merge has been made for it.
- The confirmed development catalog for this project is
  `dev_natalia_correia_lh_nautical`.
- `dev_natalia_correia_lh_nautical.rag` and
  `dev_natalia_correia_lh_nautical.rag.document_chunks` **exist** and have
  been physically validated against the runtime (17 columns, correct types,
  correct physical `NOT NULL` set, `delta.enableChangeDataFeed = true`).
  The table has been runtime-populated: **163 rows**, `COUNT(DISTINCT
  chunk_id) = 163` — see "Delta Runtime Validation Results" below.
- **Former runtime blocker (fixed, runtime-confirmed):** `run_ingestion.py`
  used to resolve its own location via `Path(__file__)` unconditionally,
  but a Databricks `spark_python_task` executes its `python_file` via
  `exec(compile(source, filename, 'exec'))` inside an IPython kernel
  context, which does not define `__file__`. Every attempt to run
  `run_ingestion.py` as a `spark_python_task` used to fail immediately with
  `NameError: name '__file__' is not defined`, before any ingestion logic
  ran. This is **fixed**: `run_ingestion.py` now uses `Path(__file__)` when
  available and falls back to `Path.cwd()` otherwise, and in both cases
  locates the `rag/` root structurally (confirming `knowledge_base/`,
  `src/`, `sql/` are present) rather than trusting either anchor blindly —
  see "Persistence Layer" and "Delta Runtime Validation Results" below. The
  fix has been runtime-validated: two full ingestion runs completed
  successfully as real `spark_python_task` job submissions.
- The shared `rag_endpoint` (Databricks AI Search, `STANDARD`, created by
  Adriano Takata) was observed as `ONLINE` with `num_indexes = 0`
  immediately before this project created its own index there. Who/when a
  previously-observed index (from an earlier, unrelated observation) was
  removed is unknown and not asserted here. **Freshly re-verified for this
  revision** (`databricks vector-search-endpoints get-endpoint`/
  `list-indexes`/`get-index`, read-only): `rag_endpoint` is `ONLINE` with
  `num_indexes = 1`; this project's own index,
  `document_chunks_index`, is `ready = true`, `HYBRID`, `TRIGGERED`,
  `indexed_row_count = 163` — consistent with the Delta table's
  last-documented 163-row state below — see "Retrieval Architecture" below.

**Delta Runtime Validation Results (runtime-validated, not a design
expectation):**

| Run | Inserts | Updates | Deletes | Total rows |
|---|---|---|---|---|
| First identical-corpus run (5 docs) | 99 | 0 | 0 | 99 |
| Immediate identical rerun (5 docs) | 0 | 0 | 0 | 99 (unchanged) |
| Sixth-document addition (Power BI promotion, 6 docs) | 64 | 0 | 0 | 163 |

All three rows reflect real Databricks job runs (`spark_python_task`,
`databricks jobs submit`), not projected/expected values. `DESCRIBE
HISTORY` shows `version 0 = CREATE TABLE`, `version 1 = MERGE
(numSourceRows=99, numTargetRowsInserted=99, numTargetRowsUpdated=0,
numTargetRowsDeleted=0)`, `version 2 = MERGE (numSourceRows=99,
numTargetRowsInserted=0, numTargetRowsUpdated=0, numTargetRowsDeleted=0)`,
`version 3 = MERGE (numSourceRows=163, numTargetRowsInserted=64,
numTargetRowsUpdated=0, numTargetRowsDeleted=0, numOutputRows=64)`. CDF
(`table_changes`) confirms 99 `insert` records for version 1, zero records
for version 2, and 64 `insert` records for version 3 — all 64 belonging to
`document_id = powerbi_semantic_rules`, zero CDF events touching any of the
five pre-existing documents. Two distinct runtime behaviors are now
demonstrated: (A) an identical rerun leaves all rows unchanged (version 2),
and (B) adding a sixth canonical document inserts only that document's
chunks while the 99 pre-existing chunks remain byte-identical — same
`chunk_id` set, same `content_hash`, same `document_hash`, same
`ingested_at`, zero rows removed or recreated (verified by snapshot
comparison before/after version 3).

**Latest complete local test run for the Phase 1 ingestion test suite:**

```
232 passed, 0 failed, 0 skipped
```

(previously 229 passed on the 5-document corpus; the increase to 232
reflects three existing tests that are parametrized directly over
`CANONICAL_DOCUMENTS` and therefore automatically gained one additional
case each for the sixth document — no test was weakened to reach this
number. This 232 figure covers only the Phase 1 ingestion-scoped test
files — it is not the whole-repository count. See "Testing" below for the
current whole-repository figure: **1208 passed, 0 failed, 0 skipped** — the
last full-suite aggregate actually executed, now including the
Harness/Tracing/Citations/App and the structured-data/Structured Analytics
(registry, compatibility, parser, compiler, renderer) test files.)

## Knowledge Provenance — Distinct Origins

This distinction is critical and must not be conflated. The current
canonical corpus has **two** curated, human-validated provenance paths
(A and B below); external/client raw documents (C) remain a separate,
future, not-yet-implemented pipeline.

### A. Data/Gold/dbt/SQL-curated knowledge — IMPLEMENTED

Five of the six canonical Markdown documents currently used by the RAG
were **not** generated from any project PDF. They were produced through
direct data exploration and validation against the Gold layer:

- Gold tables;
- dbt models;
- SQL;
- data exploration;
- business-rule investigation;
- evidence validation against the live schema/tests.

These five documents are:

- `gold_business_rules.md`
- `inventory_and_stockout.md`
- `demand_forecasting.md`
- `metrics_dictionary.md`
- `gold_data_architecture.md`

Their lineage is:

```
Data / Gold / dbt / SQL / analysis
        |
        v
human-curated and validated knowledge
        |
        v
canonical Markdown
        |
        v
RAG ingestion (loader -> parser -> chunker -> persistence)
```

### B. PBIP/TMDL-curated Power BI semantic knowledge — IMPLEMENTED

The sixth canonical document, `powerbi_semantic_rules.md`, has a distinct
but equally curated/validated provenance: direct inspection of the
`Dashboard Gerencial Compass 360 - LH Nautical` PBIP project's TMDL
(Semantic Model) and JSON (Report) artifacts, not inference or
free-form authoring. Its lineage is:

```
PBIP / TMDL (Semantic Model) + JSON (Report) artifacts
        |
        v
direct inspection (measures, relationships, filters, report semantics)
        |
        v
human-curated and validated knowledge
        |
        v
canonical Markdown (powerbi_semantic_rules.md)
        |
        v
RAG ingestion (loader -> parser -> chunker -> persistence)
```

The indexed/retrievable artifact is `powerbi_semantic_rules.md` itself —
the raw PBIP/TMDL project files are **not** directly indexed and are not
part of the RAG pipeline; they were consulted only as the evidence source
for curating this one Markdown document. See "Power BI Semantic
Knowledge" below for what it covers.

### C. External / client documents — NOT YET PART OF THE CORPUS

PDFs and future client documents (manuals, policies, procedures, etc.)
represent a **different** source of knowledge, with a different intended
pipeline (see "Future Document Preprocessing Pipeline" below). They are
preserved as original/raw/reference material and are planned to be handled
by a future document-ingestion pipeline, not by hand-curation.

**These PDFs are not currently part of the canonical retrieval corpus.**
There is no implemented or even partial relationship between the existing
six Markdown files (five Gold/dbt-curated plus the PBIP/TMDL-curated Power
BI document) and any PDF — the diagrams and sections below that describe a
PDF-to-Markdown path are entirely PLANNED/future architecture. PBIP/TMDL
inspection (path B above) is not the same pipeline as this future
PDF/DOCX/PPTX path, and must not be conflated with it.

## Architecture — Current, Implemented

```
Canonical Markdown
        |
        v
   loader.py
        |
        v
  LoadedDocument
        |
        v
   parser.py
        |
        v
ParsedDocument / ParsedSection
        |
        v
   chunker.py
        |
        v
     Chunk[]                                    <- IMPLEMENTED, up to here
        |
        v
[RUNTIME VALIDATED] persist_chunks()
  (validate -> 16-col source DataFrame -> validate target -> MERGE)
        |
        v
[RUNTIME VALIDATED, 163 rows] Unity Catalog
  document_chunks (17 columns, CDF enabled)
        |
        v
[RUNTIME VALIDATED, Phase 3A] Databricks AI Search
  document_chunks_index (TRIGGERED, Qwen3 managed embeddings, HYBRID)
        |
        v
[RUNTIME VALIDATED, Phase 4C] Retrieval Policy (query classification)
        |
        +-- EXACT_RULE_ID   -> HYBRID + rule_ids filter / Top-K=5
        |
        +-- CROSS_SOURCE    -> HYBRID / Top-K=10
        |
        +-- NORMAL          -> HYBRID / Top-K=8
        |
        v
[RUNTIME VALIDATED, Phase 4A/4A.1/4B/4C] candidate chunks (evidence-level
  evaluated; see "Retrieval Architecture" below)
        |
        v
[RUNTIME VALIDATED, Phase 5A] orchestration/rag_pipeline.py
  (RagOrchestrator: evidence translation, LlamaIndex retriever adapter,
  deterministic source labeling, context/prompt assembly)
        |
        v
[RUNTIME VALIDATED, Phase 5A] generation/llama_client.py
  (DatabricksLlamaLLM, a LlamaIndex CustomLLM)
        |
        v
[RUNTIME VALIDATED, Phase 5A] Databricks-hosted Llama
  (databricks-meta-llama-3-3-70b-instruct, Foundation Model API)
        |
        v
   grounded answer + deterministic source metadata
        |
        v
[IMPLEMENTED, Phase 6A] AIHarness.ask()
  (src/harness/harness.py — request validation, AI_SEARCH source route,
   controlled-failure containment; calls RagOrchestrator.generate()
   unchanged, adds no retrieval/generation logic of its own)
        |
        v
[IMPLEMENTED, Phase 7] traced_ask()
  (src/harness/tracing.py — one MLflow parent span around AIHarness.ask,
   nesting the autologged retrieval/generation spans into one trace)
        |
        v
[IMPLEMENTED] check_citation_integrity()
  (src/evaluation/citations.py — structural check of the model's [Sn]
   markers against the deterministic sources metadata; called by the App,
   not by the Harness itself)
        |
        v
[IMPLEMENTED, DEPLOYED dev, LIVE VALIDATED, Phase 8] Databricks App (rag/app.py)
  POST /api/ask -> stable JSON contract (status/answer/sources/
  citation_integrity/message) — see "Interface Layer — Databricks App"
        |
        v
   ... see "Final Architecture — Six Layers" below for what is still
       future within Layers 5–6 (live structured-data validation, Phase
       6B.2; context/token-budget selection; a full chatbot UI beyond this
       API; and the MLflow evaluation harness half of the Quality plane) —
       this diagram now covers all six layers at least partially, and the
       Knowledge/Data, Retrieval, Orchestration, and Generation layers are
       fully implemented and runtime-validated. The AI Harness routes
       between `AI_SEARCH`, `STRUCTURED_DATA` and `BOTH` (Phase 6B.1,
       implemented and unit-tested locally, **not live** — the currently
       deployed App does not contain this code and has no SQL Warehouse
       configured, so only the `AI_SEARCH` path has been validated end to
       end); general evidence-sufficiency judgment
       beyond the empty-retrieval guard, and MLflow's evaluation (as
       opposed to tracing) half, remain not implemented.
```

Everything up to and including `Chunk[]` is implemented and tested. The
Delta Persistence layer (pure-Python validation/contracts and Spark/Delta
runtime code, including `run_ingestion.py`) is implemented and
runtime-validated: `document_chunks` exists in Unity Catalog, its physical
schema has been runtime-validated, and a real `MERGE` has been executed
three times (first ingestion of the original 5 documents: 99 inserted;
identical rerun: 0 inserted/0 updated/0 deleted, 99 unchanged; addition of
the sixth canonical document, `powerbi_semantic_rules`: 64 inserted/0
updated/0 deleted, 99 unchanged, 163 total) — see "Delta Runtime
Validation Results" above and "Persistence Layer" below. The Retrieval
Layer's own index, `document_chunks_index`, has also been created,
runtime-validated (Phase 3A), and kept in sync with the current 163-row
corpus via a single `TRIGGERED` sync after the sixth-document addition.
The Retrieval Policy layer (Phase 4C) — deterministic query
classification into `EXACT_RULE_ID` / `CROSS_SOURCE` / `NORMAL`, each with
its own evidence-derived Top-K — is also implemented and runtime-validated
on top of that index; see "Retrieval Architecture" below for the full
Phase 4A/4A.1/4B/4C evidence chain. This retrieval policy is retrieval-layer
logic only — it is not the AI Harness (Layer 5) and does not decide
between AI Search and the future structured Delta/SQL data path; the
Harness (Phase 6A) calls it indirectly, through `RagOrchestrator.generate`,
unchanged — it does not own or reimplement it.

As of Phase 5A, the pipeline no longer stops at candidate chunks: a
LlamaIndex-based orchestrator (`orchestration/rag_pipeline.py`) consumes
the Retrieval Policy's output unchanged, translates it into LlamaIndex
nodes, assembles a deterministic grounding context, and calls a
Databricks-hosted Llama model (`generation/llama_client.py`) to produce a
grounded answer plus deterministic source metadata — end to end, this
path is now implemented and runtime-validated (see "Orchestration Layer —
LlamaIndex" and "Generation Design" below for the full detail, including
known limitations). LlamaIndex orchestrates execution; it does not
re-decide retrieval routing, Top-K, or filtering — those remain owned
entirely by the Phase 4C policy.

As of Phase 6A/7/8, the pipeline no longer stops at a generated answer
either: `AIHarness.ask` (`harness/harness.py`) wraps
`RagOrchestrator.generate` with request validation and a controlled
`AI_SEARCH`-route result; `traced_ask` (`harness/tracing.py`) wraps that
call in one MLflow parent span per request; `check_citation_integrity`
(`evaluation/citations.py`) structurally validates the model's citations;
and a FastAPI Databricks App (`app.py`) exposes all of it as `POST
/api/ask`, deployed to the `dev` target via the existing root Bundle. What
remains planned within these layers: live validation of the
structured-data routes (`STRUCTURED_DATA`/`BOTH` — implemented locally in
Phase 6B.1, live in Phase 6B.2), context/token-budget selection (see "Top-K vs Token Budget"),
semantic-groundedness/evidence-sufficiency judgment beyond the Phase 5A
empty-retrieval guard, a full chatbot UI beyond this API contract, and the
MLflow evaluation-harness half of the Quality plane (tracing itself is
implemented; systematic quality regression is not).

## Final Architecture — Six Layers

The RAG is **not** "Vector Search + LLM." The approved target architecture
is a six-layer system, plus a transversal Quality/Observability plane:

1. **Knowledge/Data Layer** — canonical Markdown → loader → parser →
   chunker → deterministic IDs/hashes/provenance → Delta `document_chunks`
   → atomic MERGE → CDF. Design FINAL/CLOSED; RUNTIME VALIDATED (see
   Status).
2. **Retrieval Layer** — Databricks AI Search / Hybrid Search, via this
   project's own `document_chunks_index` over `document_chunks`, on the
   shared `rag_endpoint`, fronted by a deterministic retrieval policy
   (Phase 4C: `EXACT_RULE_ID`/`CROSS_SOURCE`/`NORMAL` routing). IMPLEMENTED
   / RUNTIME VALIDATED, including a formal evidence-level evaluation
   (Phase 4A/4A.1), controlled identifier experiments (Phase 4B), and
   policy runtime validation (Phase 4C) — see "Retrieval Architecture".
   Reranking, generic-heading pollution, and OOD evidence sufficiency
   remain open (see "Retrieval Quality — Known Limitations").
3. **Generation Layer** — Databricks-hosted Llama via Foundation Model
   API (`databricks-meta-llama-3-3-70b-instruct`), called through
   `generation/llama_client.py`. IMPLEMENTED / RUNTIME VALIDATED
   (Phase 5A — see "Generation Design").
4. **Orchestration Layer** — LlamaIndex, connecting retrieval and
   generation at runtime via `orchestration/rag_pipeline.py`. IMPLEMENTED
   / RUNTIME VALIDATED (Phase 5A — see "Orchestration Layer —
   LlamaIndex"). Consolidated into one lean module after the Architecture
   Scope Review (see "Roadmap").
5. **AI Harness / Quality Layer** — custom application-control layer:
   request validation, source routing, citation validation. IMPLEMENTED and
   live-validated for the `AI_SEARCH` route (Phase 6A — see "AI Harness");
   `AI_SEARCH`/`STRUCTURED_DATA`/`BOTH` routing is IMPLEMENTED and
   unit-tested locally (Phase 6B.1) but NOT LIVE until Phase 6B.2 (see
   "Structured Data & Source Routing"). Context/token budget and guardrails
   beyond the Phase 5A empty-retrieval guard remain PLANNED.
6. **Interface Layer** — Databricks App. Backend IMPLEMENTED and DEPLOYED
   to `dev` (`POST /api/ask`, Phase 8 — see "Interface Layer — Databricks
   App"); full live-request validation is currently blocked on a Unity
   Catalog permission grant. A chatbot UI beyond this API contract remains
   PLANNED.

Transversal: **MLflow** — request tracing IMPLEMENTED (Phase 7); the
evaluation harness half remains PLANNED — see "Quality, Evaluation and
Observability" below.

```
                         DATABRICKS APP
                                |
                                v
                         +--------------+
                         |  AI HARNESS  |
                         | routing      |
                         | validation   |
                         | context      |
                         | evidence     |
                         | citations    |
                         +------+-------+
                                |
                                v
                           LLAMAINDEX
                       /        |        \
                      /         |         \
                     v          v          v
              AI SEARCH     DELTA / SQL   LLAMA
              Hybrid        Structured    Foundation
              Search          Data          API
                 |              |
                 v              v
          document_chunks   predictions /
                            analytical views

                   QUALITY / OBSERVABILITY
                           MLFLOW
                    ├── Tracing
                    ├── Evaluation Harness
                    ├── Retrieval Quality
                    ├── Answer Quality
                    ├── Faithfulness
                    ├── Citation Quality
                    ├── Regression Tests
                    ├── Latency
                    └── Usage / Cost
```

This is still the same six-layer architecture — there is no seventh "ML"
layer. The `DELTA / SQL` branch under LlamaIndex is the same Knowledge/Data
Layer's physical storage (Unity Catalog), used through a **different access
path** for dynamic structured data (see "Structured Runtime Data —
Analytical / ML Outputs (PLANNED)" below), not a new architectural layer.
The diagram above is still, in one respect, **target** architecture: it
shows the AI Harness routing between `AI Search`, `Delta/SQL`, and `Llama`,
but only the `AI Search` branch is real today — the Harness's `AI_SEARCH`
route is implemented and calls the same Phase 4C-driven
`AI Search -> Llama` path via LlamaIndex. The `Delta/SQL` branch (one
analytical intent over the Gold layer) and the routing decision between the
two are implemented and unit-tested locally (Phase 6B.1) but **not live**:
no SQL Warehouse is configured for the App yet (Phase 6B.2). Ranked
structured values never pass through the model — they are rendered by
code. See "Architecture — Current, Implemented" above for the diagram that
reflects what actually runs today, and "Structured Data & Source Routing".

Layer 1 (Knowledge/Data), Layer 2 (Retrieval, via `document_chunks_index`
and the Phase 4C Retrieval Policy), Layer 3 (Generation, Databricks Llama)
and Layer 4 (Orchestration, LlamaIndex) are **implemented and
runtime-validated** today (Phase 5A) — see Status. Layer 5 (AI Harness) is
implemented and live-validated for the `AI_SEARCH` route (Phase 6A) and
routes to structured data locally (Phase 6B.1, not live); Layer 6 (Interface,
Databricks App) is implemented, deployed to `dev`, and **live
end-to-end validated** (Phase 8) — the backend/API surface, not the
chatbot UI, which remains separate ongoing work. The
`DELTA / SQL` structured-data query path shown above is **IMPLEMENTED
LOCALLY as generic Structured Analytics over the Gold layer (Phase 6B.1 and its generalization) but NOT LIVE**; ML/forecast
outputs remain deferred until a real data contract exists. MLflow
tracing (part of the transversal plane) is implemented and live-validated;
the MLflow evaluation harness is **PLANNED**.

## Architecture — Target, Multi-Source (PLANNED)

This diagram is specifically about Layer 1's future knowledge-intake path
(how a second, external source of documents could eventually reach the
same canonical Markdown / ingestion pipeline) — it is a detail of Layer 1
above, not a separate architecture. The target architecture adds a second
upstream knowledge path (external
client documents) that converges with the existing data-exploration path
at the canonical knowledge layer. Everything above the "Canonical Markdown"
convergence point below already exists (see previous section); everything
feeding into it from the right-hand branch (client documents) is PLANNED.
Below `document_chunks`, `Databricks AI Search`, `Hybrid Retrieval`,
`Retrieval Policy routing`, `Top-K Candidates`, and the LlamaIndex
orchestration + Databricks Llama generation step are now runtime-validated
(see "Retrieval Architecture" and "Orchestration Layer — LlamaIndex") —
but only for the existing six-document corpus reachable today; the
Context Manager/token-budget step remains PLANNED, and Response
Verification is PARTIAL (see "Response Verification / Groundedness") —
none of this diagram's left-hand "Data exploration" branch changes — this
diagram is still specifically about the future external-document intake
path on the right.

```
                          KNOWLEDGE SOURCES

    Data exploration                         Client documents
    Gold / dbt / SQL           [EXISTING]      PDF / DOCX / PPTX / HTML   [PLANNED]
           |                                          |
           | curated knowledge                        | parsing
           v                                          v
    Canonical Markdown                      Structured document
     [EXISTING/CLOSED]                                |
                                                 normalization
                                                       |
                                                 quality gates
                                                       |
                                AUTO_APPROVED / REVIEW_REQUIRED / REJECTED
                                                       |
                                                       v
                                              Canonical Markdown
                                                       |
                        ---------------------------------
                                       |
                                       v
                               Canonical Knowledge Base
                                       |
                                       v
                                    loader           [EXISTING/CLOSED]
                                       |
                                    parser           [EXISTING/CLOSED]
                                       |
                                   chunker           [EXISTING/CLOSED]
                                       |
                               document_chunks       [RUNTIME VALIDATED]
                                       |
                              Databricks AI Search    [RUNTIME VALIDATED]
                                       |
                                Hybrid Retrieval       [RUNTIME VALIDATED]
                                       |
                          Retrieval Policy routing      [RUNTIME VALIDATED]
                                       |
                               Top-K Candidates       [RUNTIME VALIDATED]
                                       |
                        Context Manager / Token Budget [PLANNED]
                                       |
                       LlamaIndex orchestration +      [RUNTIME VALIDATED,
                       Databricks Llama generation       Phase 5A]
                                       |
                          Response Verification         [PARTIAL]
                                       |
                          Answer + Evidence/Citations
```

The Retrieval Policy, Top-K routing, LlamaIndex orchestration, and
Databricks Llama generation stages above are implemented and
runtime-validated **for the existing six-document corpus** (see
"Orchestration Layer — LlamaIndex" and "Generation Design" below) — this
diagram is otherwise still about the **future** multi-source document
path; the Context Manager/token-budget step and formal Response
Verification remain PLANNED regardless of source.

This diagram does **not** imply that the existing canonical Markdown came
from PDF processing — the two upstream branches are and remain distinct
(see "Knowledge Provenance" above).

## Canonical Knowledge Base

The authorized corpus contains exactly six Markdown documents, all
`document_status = CLOSED`:

| File | `document_id` | `canonical_domain` |
|---|---|---|
| `rag/knowledge_base/gold_business_rules.md` | `gold_business_rules` | `business_rules` |
| `rag/knowledge_base/inventory_and_stockout.md` | `inventory_and_stockout` | `inventory_stockout` |
| `rag/knowledge_base/demand_forecasting.md` | `demand_forecasting` | `demand_forecasting` |
| `rag/knowledge_base/metrics_dictionary.md` | `metrics_dictionary` | `metrics` |
| `rag/knowledge_base/gold_data_architecture.md` | `gold_data_architecture` | `data_architecture` |
| `rag/knowledge_base/powerbi_semantic_rules.md` | `powerbi_semantic_rules` | `powerbi_semantics` |

`loader.py` never discovers documents by scanning the directory — it reads
this exact allowlist. The current corpus is these six documents / 163
chunks, runtime-validated (see "Delta Runtime Validation Results" above).
No PDF, PBIX, or raw PBIP/TMDL project file is treated as a canonical
source directly — only the curated `powerbi_semantic_rules.md` Markdown
derived from PBIP/TMDL inspection is (see "Knowledge Provenance" above).

### Power BI Semantic Rules — FINAL / CLOSED / INGESTED / RUNTIME VALIDATED

`powerbi_semantic_rules.md` is part of `CANONICAL_DOCUMENTS`
(`document_id = powerbi_semantic_rules`, `canonical_domain =
powerbi_semantics`, `document_status = CLOSED`) and has been promoted,
ingested, and runtime-validated end to end: 64 deterministic chunks
persisted via a real Delta `MERGE`, synced into `document_chunks_index`,
and confirmed retrievable — see "Delta Runtime Validation Results" and
"Retrieval Architecture" above/below.

It was derived from direct inspection of the `Dashboard Gerencial Compass
360 - LH Nautical` PBIP project's TMDL/JSON artifacts (not free-form
inference) and covers, concisely:

- the Semantic Model's tables and their origin (`prod_lh_nautical.gold.*`);
- all 63 DAX measures (57 business measures documented individually, 6
  presentation/technical measures);
- the 47 relevant relationships (cardinality, cross-filter behavior);
- semantic filter behavior persisted in visuals (report semantics, not
  business rules);
- report-level semantics (pages, persisted filters);
- Power BI's own implementation of concepts already covered by existing
  Gold documents (e.g. `GOLD-004`, `GOLD-008`, `GOLD-017`, `GOLD-029`,
  `GOLD-032`), cross-referenced rather than redefined;
- explicit provenance to the internal PBIP/TMDL artifact paths that
  support each group of knowledge (e.g. `definition/tables/_Medidas.tmdl`,
  `definition/relationships.tmdl`).

Power BI semantic-model knowledge is stable, documentary knowledge — it
belongs to the same Markdown/AI Search path as the other five documents,
**not** to the Delta/SQL dynamic-data path (see "Structured Runtime Data —
Analytical / ML Outputs (PLANNED)"). Its actual, now-completed path was:

```
PBIP/TMDL evidence
        |
        v
curated powerbi_semantic_rules.md
        |
        v
canonical Knowledge Base
        |
        v
   document_chunks           (RUNTIME VALIDATED: 64 chunks inserted)
        |
        v
Databricks AI Search          (RUNTIME VALIDATED: synced to 163/163)
```

This was the "Corpus Completion Checkpoint" in the Roadmap below — now
**PASS / CLOSED**.

## Ingestion Pipeline

### Loader

`rag/src/ingestion/loader.py` — FINAL / CLOSED.

- Explicit allowlist of the 6 canonical documents (`CANONICAL_DOCUMENTS`);
  loads exactly these, in deterministic declaration order.
- Text normalization (`normalize_text`), applied in order: strip a leading
  UTF-8 BOM if present, normalize Unicode to NFC, normalize line endings
  (CRLF/CR → LF). Everything else in the document is preserved verbatim
  (no whitespace collapsing, no stripping, no case changes).
- `document_hash`: SHA-256 hex digest of the normalized text.
- `source_git_commit`: filled in **only** when the ingested content is
  provably identical (after normalization) to the version tracked at
  `HEAD` for that path. Untracked files, uncommitted changes, or content
  diverging from `HEAD` all resolve to `source_git_commit = NULL` — never a
  bare `git rev-parse HEAD`.
- No responsibility for Markdown structure, Rule ID extraction, chunking,
  or embeddings — strictly out of scope for this module.

### Parser

`rag/src/ingestion/parser.py` — FINAL / CLOSED.

- Exactly one H1 required per document; H1 becomes `document_title` and
  never enters `section_path`/`section_title`. Non-empty content before the
  H1 raises `ParserError`.
- H2–H6 form the structural hierarchy used in `section_path`, joined with
  the separator `" > "`. Heading level jumps (e.g. H2 straight to H4) are
  allowed without synthesizing artificial intermediate ancestors.
- Content after the H1 and before the first H2+ becomes one introductory
  `ParsedSection` with `section_path = NULL` and `section_title = NULL`.
- Headings written inside fenced code blocks are not treated as structural
  headings.
- Rule IDs are extracted with `\bGOLD-\d{3}\b` and deduplicated preserving
  first-occurrence order (not alphabetical/numerical).
- Provenance: `line_start`/`line_end`, 1-indexed, inclusive.

### Chunker

`rag/src/ingestion/chunker.py` — FINAL / CLOSED, including the
retrieval-quality micro-patch described below.

- Primary semantic unit: `ParsedSection`. Default rule:
  **1 content-bearing `ParsedSection` → 1 `Chunk`**.
- A section is only split internally when its estimated token count
  exceeds `max_tokens`; `target_tokens` is a preference, never an
  obligation — a section under the hard max is always kept whole.
- **Heading-only retrieval policy:** a `ParsedSection` whose text is only
  its own heading line, optionally followed by blank/whitespace-only
  lines, produces **no** `Chunk`. This is a structural decision, not a
  token-size heuristic — there is no minimum-token threshold. The parser
  still preserves the section for hierarchy purposes; the chunker simply
  does not index it as a retrieval unit. Descendant sections keep their
  full `section_path`, so hierarchical context is not lost.

  Example:

  ```
  ## Vendas
  ```
  → structure only, no chunk.

  ```
  ### Vendas efetivas

  Regra...
  ```
  → generates a chunk (real content beyond the heading).

  A short section with real textual content is always indexed regardless
  of length — the criterion is presence of content, not size.

## Chunking Strategy v1

| Parameter | Value |
|---|---|
| Target | 512 estimated tokens |
| Hard max | 800 estimated tokens |
| Overlap | 64 estimated tokens (intra-section only) |
| Overlap between different `ParsedSection`s | 0 (never crosses a section boundary) |

- `target_tokens` is a preference; a section at or under `max_tokens` is
  never split just for exceeding `target_tokens`.
- A section over `max_tokens` is split internally, preferring boundaries in
  this order: fenced/structural blocks → paragraph/blank-line blocks →
  lists/tables when possible → individual lines → lexical (word-boundary)
  fallback only as a last-resort edge case.
- Token counting: `estimate_token_count`, a deterministic, stdlib-only
  lexical heuristic. It is **not** a tokenizer for any specific embedding
  model. The chunker accepts an injectable
  `TokenCounter = Callable[[str], int]` so a real tokenizer can be plugged
  in later without redesigning the splitting/packing/overlap algorithm.
  At this stage, connecting an exact tokenizer is **NOT REQUIRED** — no
  tokenizer dependency has been added, and Chunking Strategy v1 (512/800/64)
  is kept as-is.

These decisions are closed for the MVP. No proposal to change chunk sizing,
switch to semantic/parent-child chunking, or alter fixed-size behavior
should be entertained without new evidence from retrieval evaluation.

## Retrieval Units — Current Corpus

Computed from a real run of loader → parser → chunker over the 6 canonical
documents (not estimated):

| Metric | Value |
|---|---|
| Canonical documents | 6 |
| `ParsedSection`s | 186 |
| Heading-only sections skipped | 23 |
| Final retrieval chunks | 163 |
| Sections split (exceeded hard max) | 0 |
| Chunks above hard max | 0 |
| Overlap actually used | 0 |
| Lexical fallback actually used | 0 |
| Maximum estimated tokens in any chunk | 642 |

Per-document chunk count:

| Document | Chunks |
|---|---|
| `gold_business_rules` | 17 |
| `inventory_and_stockout` | 19 |
| `demand_forecasting` | 12 |
| `metrics_dictionary` | 29 |
| `gold_data_architecture` | 22 |
| `powerbi_semantic_rules` | 64 |
| **Total** | **163** |

The original 5-document corpus (99 chunks) is preserved as a baseline in
"Delta Runtime Validation Results" above, to document the first two
runtime behaviors (first insert, identical rerun) that were validated
before the sixth document existed.

No section in the current corpus is large enough to trigger internal
splitting, overlap, or the lexical fallback path — those code paths are
implemented and tested, but not exercised by the current 6-document corpus.

## Chunk Contract

The in-memory `Chunk` dataclass has exactly **16 fields**:

```
chunk_id
chunk_index
document_id
document_title
source_path
canonical_domain
document_status
section_path
section_title
chunk_text
rule_ids
line_start
line_end
content_hash
document_hash
source_git_commit
```

`ingested_at` does **not** belong to `Chunk` — it is generated only inside
the Delta `MERGE` itself (`current_timestamp()`), never carried in the
in-memory `Chunk` or in the 16-column source DataFrame built from it. `Chunk`
also carries no embedding field: `Chunk in memory = 16 fields`, `Delta
document_chunks = 17 columns` (16 + `ingested_at`). See "Persistence Layer"
below for how this split is implemented.

### Hashing / identity

```
content_hash = SHA-256( normalize_text(chunk_text) )

chunk_id = SHA-256( canonical_json([
    document_id,
    section_path or "",
    chunk_index,
    content_hash,
]) )
```

- No UUIDs, no `hash()`, no random identifiers.
- `document_hash`, `source_git_commit`, and `ingested_at` are **not** part
  of `chunk_id`'s identity — only `document_id`, `section_path`,
  `chunk_index`, and `content_hash` are.
- `document_hash` identifies the full source document version; all chunks
  from the same document version share the same `document_hash`.

### Rule IDs on the chunk

Rule IDs are **recomputed on the final `chunk_text`** with
`\bGOLD-\d{3}\b`, deduplicated preserving first-occurrence order — never
blindly inherited from `ParsedSection.rule_ids` (a split chunk may
legitimately drop or, via overlap, duplicate a Rule ID relative to its
parent section). A chunk with no Rule ID yields an empty tuple in Python
and an empty `ARRAY<STRING>` in the Delta table — never `NULL`.

## Persistence Layer

Delta Persistence Design is FINAL / CLOSED. Implementation is complete on
both the pure-Python side and the Spark/Databricks side, and both have been
runtime-validated — see "Delta Runtime Validation Results" above.

**Module boundary** (see `rag/src/ingestion/`):

| Layer | Modules | Requires PySpark |
|---|---|---|
| Pure Python | `schema_contract.py`, `persistence_validation.py`, `persistence_expressions.py` | NO |
| Databricks/Spark runtime | `spark_schema.py`, `persistence.py`, `run_ingestion.py` | YES |

The schema contract is defined in Python-native `FieldSpec` structures
(`schema_contract.py`) and materialized into PySpark `StructType` only at
Databricks runtime (`spark_schema.py`, via `build_chunk_source_schema()` /
`build_document_chunks_schema()`) — local tooling never requires PySpark to
inspect or validate the contract. `persistence_validation.py`
(`validate_chunks_for_persistence`) and `persistence_expressions.py` (the
identifier parsing, SQL literal escaping, and MERGE condition/insert/update/
delete expression builders) are pure Python for the same reason: local
tests must be able to import and run them without PySpark installed.
`persistence.py` imports both rather than redeclaring any of that logic.

**Entrypoint import mode:** a Databricks `spark_python_task` executes its
`python_file` as a standalone script, never as a package module, so
`run_ingestion.py` cannot reach its sibling project modules via relative
imports the way `persistence.py`/`spark_schema.py` reach *their* own
siblings. `run_ingestion.py` locates the `rag/` root, inserts it onto
`sys.path`, and imports its own dependencies via the same absolute
`src.ingestion...` path the test suite already uses — the internal modules
it then loads keep their existing relative imports unchanged. It also sets
`sys.dont_write_bytecode = True` before those imports, mirroring
`tests/run_tests.py`'s own guard against the Databricks Workspace
filesystem's inability to write `__pycache__` (a failure mode this repo
has hit before).

**Runtime result (confirmed, fixed):** a Databricks `spark_python_task`
does not execute its `python_file` as a real `python file.py` subprocess —
it runs `exec(compile(source, filename, 'exec'))` inside an IPython kernel
context, which never defines `__file__`. The original bootstrap, which
unconditionally used `Path(__file__)`, raised `NameError: name '__file__'
is not defined` on its very first line, before any of the relative-import
machinery it was meant to fix was even reached — reproduced identically
across multiple independent job submissions. **Fix (runtime-validated):**
`run_ingestion.py` now uses `Path(__file__).resolve().parent` as the
search anchor when `__file__` is defined (`try`/`except NameError`), and
falls back to `Path.cwd()` when it is not — the observed Databricks
behavior. Either anchor is only a starting point: `_find_rag_root()` walks
upward from it and only trusts the first ancestor directory that
structurally contains `knowledge_base/`, `src/`, and `sql/` together,
raising an explicit `RuntimeError` (never a silent/arbitrary fallback) if
no such directory is found. No workspace user, workspace id, branch, or
bundle deploy path is hard-coded. This has been validated both locally
(both branches exercised directly, plus a negative case outside the repo)
and on real Databricks: two full `spark_python_task` job submissions
completed successfully — see "Delta Runtime Validation Results" above.

**Source vs target schema:**

- `CHUNK_SOURCE_FIELDS` (16 fields) — matches the in-memory `Chunk`
  dataclass exactly; no `ingested_at`.
- `DOCUMENT_CHUNKS_FIELDS` (17 fields) — matches the physical Delta table;
  includes `ingested_at`.
- The two contracts' field orders differ (compare `document_hash`/
  `source_git_commit`/`section_path`/`section_title` between them). This is
  incidental, not architectural — correctness never depends on positional
  alignment between source and target.
- `INSERT *` is **prohibited** (arity mismatch: 16 vs 17 columns).
  `persistence.py` uses an explicit, named column mapping for every insert
  and update, with `ingested_at = current_timestamp()` supplied at `MERGE`
  time, never carried in the source.

**MERGE semantics** (`persist_chunks` in `persistence.py`):

- Key: `target.chunk_id = source.chunk_id`.
- Matched update fires only when one of `document_title`, `source_path`,
  `canonical_domain`, `document_status`, `document_hash`,
  `source_git_commit`, `line_start`, `line_end` genuinely differs
  (null-safe `<=>` comparison). Identity fields (`document_id`,
  `section_path`, `chunk_index`, `content_hash`) and content-derived fields
  (`chunk_text`, `rule_ids`) are never compared here — a real change to any
  of them produces a new `chunk_id` (delete old + insert new), never an
  in-place update.
- Insert: explicit 16-column mapping + `ingested_at = current_timestamp()`.
- Delete: `WHEN NOT MATCHED BY SOURCE AND target.document_id IN
  (processed_document_ids) THEN DELETE` — never an unscoped
  `WHEN NOT MATCHED BY SOURCE THEN DELETE`.
- All three actions execute inside a single `.merge(...).execute()` call —
  one atomic Delta transaction, never separate `DELETE`+`APPEND` statements.
- `PersistenceResult` (observability only, see below) is read from the most
  recent `DESCRIBE HISTORY` row only after confirming that row's
  `operation` is literally `"MERGE"`, and its derived `unchanged` count
  (`numSourceRows - inserted - updated`) raises `PersistenceMetricsError`
  rather than being silently returned if it comes out negative — both are
  defensive guards against reading the wrong history entry or trusting an
  internally-inconsistent metric, never silently clamped or defaulted.

**DDL ownership:** `rag/sql/create_schema.sql` and
`rag/sql/create_document_chunks.sql` own the physical DDL.
`persistence.py`'s `validate_target_table` only validates that the target
exists and matches `DOCUMENT_CHUNKS_FIELDS` (via
`information_schema.columns`) — it never creates, alters, or repairs the
table. Schema drift fails fast; no automatic correction.

**Testing boundary:** exactly two tiers, no local Delta-OSS simulation
tier. Local tests (`test_schema_contract.py`, `test_persistence_validation.py`,
`test_persistence_expressions.py`) are pure Python — no PySpark, no
`delta-spark`, no Java, no `SparkSession`. Everything requiring real
Spark/Delta/Unity Catalog behavior (DataFrame construction, live schema
validation, `MERGE` execution, CDF, `operationMetrics`) is a Databricks
integration concern — this tier has been runtime-validated on real
Databricks (see "Delta Runtime Validation Results" above), separately from
this local pure-Python suite.

## Delta `document_chunks` Contract

Logical schema, defined in
[`rag/sql/create_document_chunks.sql`](./sql/create_document_chunks.sql)
(FINAL / CLOSED as a schema contract; **applied to a physical table** —
`dev_natalia_correia_lh_nautical.rag.document_chunks` exists, has been
runtime-validated against this exact contract, and holds 163
runtime-ingested rows (99 from the original 5-document corpus + 64 from
the Power BI promotion) — see Status):

```
chunk_id           STRING
chunk_index        INT
document_id        STRING
document_title     STRING
source_path        STRING
canonical_domain   STRING
document_status    STRING
document_hash      STRING
source_git_commit  STRING
section_path       STRING
section_title      STRING
chunk_text         STRING
rule_ids           ARRAY<STRING>
line_start         INT
line_end           INT
content_hash       STRING
ingested_at        TIMESTAMP
```

Physical `NOT NULL` currently enforced in the DDL: `chunk_id`,
`chunk_index`, `document_id`, `source_path`, `chunk_text`, `rule_ids`. All
other fields marked "required" above are **logical** invariants guaranteed
by the ingestion pipeline (`loader.py`/`parser.py`/`chunker.py`), not
physical constraints — this README is the explicit source of those
invariants. No additional constraints are planned. No physical `PRIMARY
KEY` is defined; `chunk_id` is used conceptually as the primary key of the
Databricks AI Search Delta Sync index (`document_chunks_index`) — an
index-level configuration, not a Delta table constraint. See "Retrieval
Architecture" below.

**Change Data Feed:** `delta.enableChangeDataFeed = 'true'` is set on the
table contract, to support incremental synchronization with Databricks AI
Search / Delta Sync. This is now in active use: `document_chunks_index` (a
`TRIGGERED` Delta Sync Index on the shared `rag_endpoint`) consumes it —
see "Retrieval Architecture" below.

**Physical namespace:** schema `rag` is closed (a dedicated schema,
sibling to the existing dbt-managed `bronze`/`silver`/`gold`, since
`document_chunks` is not part of that lineage). Catalog remains
environment-specific — never hard-coded as
`dev_<user>_lh_nautical`/`ci_lh_nautical`/`prod_lh_nautical` in Python or
SQL. The confirmed development catalog for this project is
`dev_natalia_correia_lh_nautical` (see Status above); this value is not
hard-coded anywhere — it is passed at execution time. Since Asset Bundle
variable substitution (`${var.catalog_name}`, `databricks.yml`) applies to
YAML resource definitions, not to the literal contents of a `.sql` file,
both `rag/sql/create_schema.sql` and `rag/sql/create_document_chunks.sql`
instead use a Databricks named parameter marker, `:catalog`, combined with
the `IDENTIFIER()` clause — e.g.
`CREATE TABLE IF NOT EXISTS IDENTIFIER(:catalog || '.rag.document_chunks') (...)`.
`:catalog` is a Databricks named parameter used only by bootstrap SQL — its
value is bound at execution time (e.g. `spark.sql(sql_text, args={"catalog": ...})`
or the SQL Statement Execution API's `parameters` field), never by editing
the file. **Confirmed working:** both files were executed as-is against
`dev_natalia_correia_lh_nautical`, with `:catalog` bound at call time —
`CREATE SCHEMA`/`CREATE TABLE` both succeeded, producing the physically
validated table described above. There is still no persisted Python
bootstrap script that wraps this invocation (it was done via a direct API
call for validation purposes) — wiring it into `run_ingestion.py` or a
dedicated bootstrap step remains open (see Roadmap).

### Embedding storage

Do not manually add an embedding column to `document_chunks` while using
the Databricks-managed embedding path. The canonical Delta source stores
chunk text, metadata, hashes, and provenance. Embeddings are generated
later by Databricks AI Search / the configured embedding model, and are
not part of this table's contract.

## Document and Chunk Change Control (current + planned)

Three distinct, independent levels of change detection apply as the system
grows. Levels 2 and 3 are implemented today; level 1 belongs to the future
external-document pipeline and is PLANNED.

| Level | Hash / identity | Detects | Status |
|---|---|---|---|
| **Source** | `source_hash` | changes in an original external document (PDF/DOCX/etc.) before any parsing | PLANNED — belongs to the future document-source layer |
| **Document** | `document_hash` | changes in the canonical Markdown representation of a document | IMPLEMENTED (`loader.py`) |
| **Chunk** | `content_hash` + deterministic `chunk_id` | granular changes to one retrieval unit | IMPLEMENTED (`chunker.py`, `persistence.py`) |

The current, closed deterministic chunk-identity semantics (see "Chunk
Contract" above) drive the MERGE behavior:

- **Same chunk identity, same content** → row is left untouched (no write,
  no `ingested_at` change).
- **Metadata change with the same `chunk_id`** (e.g. `document_hash`,
  `source_git_commit`, `line_start`/`line_end` shift) → `UPDATE` in place.
- **Changed content, or a change to any field that is part of `chunk_id`'s
  identity** (`document_id`, `section_path`, `chunk_index`, `content_hash`)
  → a *new* `chunk_id`: `INSERT` the new chunk and remove the stale one,
  both **within the processed-document scope** of that MERGE run.
- **A chunk disappears from a processed, active document** (e.g. a section
  was deleted) → scoped `DELETE`, restricted to `processed_document_ids`.

**Important boundary, not to be overstated:** the scoped MERGE described
above handles chunk-level changes *within documents that are actually part
of the current run's `processed_document_ids`*. It does **not**
automatically handle an entire source document disappearing from the
source system altogether — that is a document-level (not chunk-level)
concern, addressed separately below.

## Document Retirement / Deletion Lifecycle

**Current behavior:** processed, active documents can have their stale
chunks removed through the scoped MERGE described above. An entire
document disappearing from its source is **not** currently treated as
automatic retirement — there is no implemented mechanism that detects "this
`document_id` used to be processed and no longer is" and reacts to it.

**Future design (PLANNED):**

```
active_document_ids
        |
        | compared with
        v
persisted_document_ids
        |
        v
retired_document_ids
```

Retirement is intended to be controlled by `document_id`, not `chunk_id` —
a deliberate retirement decision would eventually cause deletion of all
chunks belonging to that `document_id`, as an explicit, auditable action.

**Safety rule (important):** if a document is expected to exist but simply
disappears unexpectedly (e.g. a transient error, a misconfigured batch, a
partial sync), the system must **fail safe** — it must **not**
automatically delete all of that document's chunks. Unexpected absence is
not the same as intentional retirement, and the two must never be
conflated by the implementation.

## Embedding Model — MVP

**Primary model:** Qwen3-Embedding-0.6B. **SELECTED / AI SEARCH VALIDATED**
— no longer just a candidate (see "Retrieval Architecture" below).

| Property | Value |
|---|---|
| Foundation Model API endpoint (used by AI Search as `embedding_model_endpoint_name`) | `databricks-qwen3-embedding-0-6b` |
| Databricks AI Functions identifier (used only for the standalone `ai_query` access test below) | `system.ai.qwen3-embedding-0-6b` |
| Workspace | Databricks Free Edition |
| Foundation Model access | **CONFIRMED EMPIRICALLY** |
| AI Search managed-embedding integration | **RUNTIME VALIDATED** — accepted as `embedding_model_endpoint_name` for `document_chunks_index`, a real Delta Sync Index that reached `ready=true`, currently 163/163 rows indexed |

These are two distinct identifiers and must not be conflated:
`system.ai.qwen3-embedding-0-6b` was used only for an early, standalone AI
Functions access check (below); `databricks-qwen3-embedding-0-6b` is the
Foundation Model / serving-endpoint name actually configured on the AI
Search index.

Access was first confirmed by executing, directly in the target workspace:

```sql
SELECT ai_query(
  'system.ai.qwen3-embedding-0-6b',
  'Como é calculado o estoque de segurança?'
) AS embedding;
```

Result: an embedding vector was returned successfully. This confirmed
Foundation Model access to Qwen3-Embedding-0.6B in this Free Edition
workspace, but did **not**, by itself, confirm AI Search compatibility.
That compatibility has since been confirmed separately and conclusively:
`databricks-qwen3-embedding-0-6b` was accepted without error as the
`embedding_model_endpoint_name` of a real Delta Sync Index
(`document_chunks_index`), which reached `ready=true` with 99/99 rows
indexed at first creation and has since been kept in sync (currently
163/163, after the sixth canonical document was promoted) — see
"Retrieval Architecture" below. No other embedding model has been
formally compared against Qwen3 for this corpus; this is a validated
selection for the MVP, not a benchmarked comparison.

Qwen3-Embedding-0.6B was selected over the alternative Databricks-hosted
Foundation Model API models (`databricks-gte-large-en`,
`databricks-bge-large-en`) because both of those are English-only, while
this corpus is predominantly Portuguese with technical English and exact
identifiers mixed in.

**Alternatives considered, not part of the MVP critical path:**

- OpenAI `text-embedding-3-large` (via Databricks External Models):
  possible future evaluation challenger, outside the MVP critical path.
- BGE-M3 (self-hosted): possible self-managed fallback, not part of the
  MVP.

No dependency on either alternative has been introduced. No manual
embedding column has been or should be added to `document_chunks` (see
"Embedding storage" above) — this remains true regardless of which
embedding model is ultimately selected.

## Databricks Free Edition Constraints

The target workspace is confirmed to be Databricks Free Edition. This
shapes what the MVP architecture can rely on:

- The workspace is confirmed as Free Edition — architecture choices
  prioritize functionality available in this tier.
- Provisioned throughput is not part of the MVP (Free Edition does not
  support it).
- External/custom embedding models are not part of the critical path for
  the MVP; the primary path relies on a Databricks-managed Foundation
  Model API endpoint.
- Qwen3-Embedding-0.6B access was confirmed empirically via `ai_query`, and
  its compatibility as an AI Search managed embedding is now runtime
  validated (see "Embedding Model — MVP" and "Retrieval Architecture").
- Databricks AI Search usage must stay within Free Edition limits.
- The architecture avoids depending on paid-tier-only resources for the
  MVP critical path.
- The workspace has **one** Databricks AI Search endpoint available
  (`rag_endpoint`, `STANDARD`, already created by Adriano Takata for his
  POC). This project **reuses that endpoint** rather than creating a second
  one — multiple indexes can share one AI Search endpoint, and this
  project's own index, `document_chunks_index`, is logically separate from
  (not built on top of) Adriano's prior work there. AI Search's own compute
  covers both serving (queries) and ingestion/sync of an index; `TRIGGERED`
  sync `pipeline_type` was used for this index (validated twice: the
  initial creation synced all 99 rows automatically with no manual
  `sync()` call, and a single explicit `sync()` after the sixth canonical
  document was added brought it to 163/163), given the current corpus (163
  chunks, no active edits) is relatively stable and does not need
  continuous re-sync. The endpoint is now **actively serving this
  project's index** — it is a live, continuously
  hosted resource, not a one-time creation cost; unnecessary repeat `sync()`
  calls should still be avoided.
- Compute/fair-use in this workspace is limited and **shared across all
  projects using it**, not exclusive to this RAG effort — this project has
  already hit account-level compute blocks during validation (see
  Roadmap). Unnecessary job runs, warehouse restarts, index syncs, or
  exploratory queries should be avoided; validations should be batched into
  as few compute windows as practical. No exact DBU/billing figures are
  asserted here — see "No specific quotas..." note below.
- Generation (Llama) runs via the Foundation Model API's pay-per-token
  billing mode, not provisioned throughput.
- The Interface Layer (Databricks App, deployed to `dev` — see "Interface
  Layer — Databricks App") runs within the same Free Edition constraints —
  no additional compute tier was needed for it; its own compute is the App
  runtime container itself (`compute_size: MEDIUM`, observed via
  `databricks apps get`), separate from the SQL warehouse / job compute
  used elsewhere in this project.

No specific quotas or numeric limits are documented here beyond what is
already established above, to avoid stating figures without confirmed
evidence.

## Future Raw Document Storage (PLANNED)

**Current MVP:** Git remains the source of truth for the existing five
canonical Markdown documents. A Unity Catalog Volume is **not** required
before the first current RAG ingestion — `loader.py` reads directly from
the Git-checked-out repository.

**Future document pipeline:** original client documents (PDFs, etc.)
should preferably live **outside** Git. A Databricks Unity Catalog Volume
is the planned raw-document storage mechanism; alternatives such as
enterprise document management systems may be supported later if a
concrete need emerges.

Conceptual layout (not yet created):

```
Volume
└── source_documents/
    ├── manual.pdf
    ├── policy.pdf
    └── procedure.pdf
```

Original files are planned to be retained (never discarded after
extraction) for:

- provenance;
- auditability;
- version comparison;
- reprocessing (e.g. after a parser/extractor improvement);
- recovery.

No Volume exists or is populated today — this section describes intent
only.

## Future Document Preprocessing Pipeline (PLANNED)

Target repository structure for the future external-document path (files
not yet present unless already listed elsewhere in this README as
implemented):

```
rag/
├── knowledge_base/
├── data/
│   ├── generated_markdown/
│   └── quarantine/
├── config/
│   └── document_registry.yaml
├── src/
│   ├── preprocessing/
│   │   ├── document_detector.py
│   │   ├── pdf_extractor.py
│   │   ├── document_normalizer.py
│   │   ├── quality_gate.py
│   │   └── document_promotion.py
│   ├── ingestion/          [EXISTING]
│   ├── retrieval/          [EXISTING]
│   ├── orchestration/      [EXISTING]
│   ├── generation/         [EXISTING]
│   ├── harness/            [PLANNED]
│   └── evaluation/         [EXISTING]
├── sql/
└── tests/
```

This is still a target structure for the `preprocessing/` module
specifically (and the `data/`, `config/document_registry.yaml` paths),
none of which exist in the repository yet. `retrieval/`, `orchestration/`,
`generation/`, and `evaluation/` are annotated `[EXISTING]` above because
they already exist for the current six-document corpus (see "Retrieval
Architecture", "Orchestration Layer — LlamaIndex", "Generation Design")
— this future document-preprocessing path would feed into them, not
duplicate them. Only `harness/` remains entirely unbuilt.

Target flow:

```
source document
      |
      v
source_hash
      |
      v
document parser / extractor
      |
      v
normalization
      |
      v
generated Markdown
      |
      v
quality gates
      |
      v
promotion
      |
      v
canonical Knowledge Base
      |
      v
existing ingestion pipeline (loader -> parser -> chunker -> persistence)
```

The last step deliberately reuses the already-closed `loader.py`/
`parser.py`/`chunker.py`/persistence contracts unchanged — new documents
join the same pipeline the five current documents already go through, once
they reach canonical Markdown form.

## Quality Gates and Auto-Approval (PLANNED)

Target document-governance model. Human review is **not** required for
every document — the goal is selective review, not a manual bottleneck.

Target statuses:

- `AUTO_APPROVED`
- `REVIEW_REQUIRED`
- `REJECTED`

Candidate automatic gates for a first implementation (not yet built, not
yet tuned):

- trusted / authorized source;
- allowed file type;
- valid and readable file;
- successful parsing;
- non-empty extracted content;
- plausible text coverage;
- minimum structural validity;
- duplicate detection / source hash.

**Architectural principle:** source authority and parsing quality are
**separate concerns**. A document should only be eligible for
`AUTO_APPROVED` when **both** are satisfactory:

```
trusted source
+
technical validation PASS
        |
        v
AUTO_APPROVED

warnings
        |
        v
REVIEW_REQUIRED

critical failure
        |
        v
REJECTED / QUARANTINE
```

The precise thresholds (what counts as "plausible text coverage," which
sources are "trusted," etc.) have **not** been defined yet — see "Open
Decisions."

## Document Registry (PLANNED)

Target migration path from the current fixed allowlist
(`CANONICAL_DOCUMENTS` in `loader.py`) toward a `document_registry` that
also carries provenance and lifecycle metadata for external documents. The
current allowlist **remains valid and unchanged** for the MVP — this
section describes a future superset, not a replacement scheduled for this
phase.

Illustrative example (conceptual, not an implemented schema) for an
existing current-MVP document:

```yaml
documents:

  gold_business_rules:
    origin_type: data_exploration
    canonical_path: knowledge_base/gold_business_rules.md
    canonical_domain: business_rules
    status: APPROVED
    ingest_enabled: true
```

Illustrative example for a future external document:

```yaml
  supplier_manual:
    origin_type: client_document
    source_type: pdf
    source_path: <raw document location>
    source_hash: <sha256>
    generated_path: data/generated_markdown/supplier_manual.md
    canonical_path: knowledge_base/supplier_manual.md
    trusted_source: true
    status: AUTO_APPROVED
    ingest_enabled: true
```

The future *effective* allowlist consumed by the ingestion pipeline may be
derived from something conceptually equivalent to:

```
status IN {APPROVED, AUTO_APPROVED}
AND
ingest_enabled = true
```

Not implemented in this phase — `config/document_registry.yaml` does not
exist yet.

## Retrieval Architecture

**IMPLEMENTED / RUNTIME VALIDATED (Phase 3A + Corpus Completion Checkpoint
+ Phase 4 — Retrieval Quality & Policy).** This project's own Delta Sync
Index has been created, provisioned, kept in sync through an incremental
corpus addition, formally evaluated with an evidence-level evaluator, and
is now queried through a validated, evidence-derived routing policy — see
"Retrieval Quality Baseline (Phase 4A / 4A.1)" and "Retrieval Policy
(Phase 4C)" below. This closes Phase 4 **for the current retrieval
scope** — it does **not** mean retrieval is perfect: see "Retrieval
Quality — Known Limitations" below for what remains open (reranking,
generic-heading pollution, OOD evidence sufficiency).

**Index configuration (real, runtime-confirmed):**

- Databricks AI Search Delta Sync Index over `document_chunks`, on the
  shared `rag_endpoint` (see "Databricks Free Edition Constraints"). The
  endpoint is shared infrastructure; **the index belongs logically to this
  project's own pipeline**.
  - Index name: `dev_natalia_correia_lh_nautical.rag.document_chunks_index`.
  - Source table: `dev_natalia_correia_lh_nautical.rag.document_chunks`.
  - `pipeline_type`: `TRIGGERED` — confirmed twice: initial creation
    auto-synced all 99 rows without a manual `sync()` call, and a single
    explicit `sync()` after the sixth canonical document was added synced
    the remaining 64 rows (163/163 total).
  - Primary key: `chunk_id`.
  - Embedding text source column: `chunk_text`.
  - Embedding model: `databricks-qwen3-embedding-0-6b`, Databricks-managed
    — accepted without error, index reached `ready=true`, currently
    163/163 rows indexed (see "Embedding Model — MVP"). No embedding is
    persisted manually in `document_chunks` (see "Embedding storage").
  - `index_subtype`: `HYBRID` (assigned by the platform on creation on this
    `STANDARD` endpoint), enabling Hybrid Search at query time. Confirmed
    unchanged after the corpus-completion sync.

**Provisioning timeline (observed, initial creation with the original 5
documents):** `pending setup of pipeline resources` → `syncing initial
data` → `online, re-syncing initial data` (67/99 rows) → `Index creation
succeeded` (99/99 rows, `ready=true`), in roughly 2.5–3 minutes total,
tracked via the index status API — not via repeated `similarity_search`
polling.

**Corpus-completion sync (observed, sixth-document addition):** after the
Delta `MERGE` inserted 64 new rows for `powerbi_semantic_rules` (see
"Delta Runtime Validation Results"), a single explicit `sync()` was issued
against the existing index (no recreation). Status progressed `online,
pipeline update pending` → `Index creation succeeded` (99/163 rows,
intermediate) → `Index creation succeeded` (163/163 rows, `ready=true`),
in roughly 2.5–3 minutes, tracked the same way (status API, not
`similarity_search`).

**Hybrid Search smoke/regression tests (runtime, `query_type=HYBRID`):**
two rounds so far.

1. **Phase 3A (5-document corpus):** 5 representative Portuguese queries,
   one per category (Gold business rule, estoque/ruptura, previsão de
   demanda, métrica, arquitetura de dados), `num_results=5` each. Results
   were majority relevant and traceable; a small number of lower-ranked
   results (positions 4–5) in some queries returned only generically
   related content (e.g. a document's own "Objetivo" section) rather than
   a direct answer — recorded as input to Phase 4, not a failure.
2. **Corpus Completion Checkpoint (6-document corpus):** the same 5
   queries were rerun against the expanded 163-chunk index as a regression
   check — all 5 continued to surface correct, traceable evidence from
   their original domains (no old-domain query was displaced by the new
   Power BI content). A further 5 Power BI-specific queries (Ticket Médio
   DAX, Pedidos Abertos, `no_recent_sales` handling, Semantic Model
   measure/relationship counts, persisted report filters) all returned
   `powerbi_semantic_rules` as the top or a clearly relevant result. One
   cross-source query ("Como o Ticket Médio é definido no Gold e
   implementado no Power BI?") returned only `powerbi_semantic_rules`
   candidates within Top-8; the complementary Gold-side evidence
   (`metrics_dictionary > average_ticket_amount`) was present but only
   surfaced at rank 9 when Top-K was widened to 15 — recorded as an open,
   empirical input to Phase 4 (multi-evidence/cross-source coverage,
   Top-K), not a defect of the promotion.

Both rounds were smoke/regression validation, informal precursors to the
formal, evidence-level evaluation below — they are preserved here as
historical record, not as the current retrieval-quality evidence base.

**Retrieval metadata surfaced per candidate:** `chunk_id`, `document_id`,
`document_title`, `canonical_domain`, `section_path`, `section_title`,
`chunk_text`, `rule_ids`, and score — confirmed present in every
smoke-test response above (not merely planned).

**Explicitly not reused**: the prior POC's own index/table
(`dev_adriano_takata_lh_nautical.rag.chunks`,
`dev_adriano_takata_lh_nautical.rag.chunks_index`) are not a dependency of
this architecture — this project's index is built on
`dev_natalia_correia_lh_nautical.rag.document_chunks` only.

### Retrieval Quality Baseline (Phase 4A / Phase 4A.1)

**Phase 4A — first formal evaluation (historical methodology).** Produced
a versioned dataset (`rag/evaluation/dataset/retrieval_queries_v1.jsonl`,
37 queries) covering `business_rules`, `inventory`, `forecasting`,
`metrics`, `data_architecture`, `powerbi`, `cross_source`, and
`negative`/OOD, plus a Top-K experiment (K=3/5/8/10/15), latency
measurements, a generic-heading analysis, and negative/OOD diagnostics.
**This first evaluator had a methodological bug**: some of its metrics
(`Hit@K`, `Evidence Coverage@K`) counted "the expected *document* appeared
in Top-K" as success, regardless of which chunk/section actually
appeared — i.e. it sometimes treated *correct document retrieved* as
*correct evidence retrieved*. Its raw numbers are preserved only as
historical baseline artifacts (`rag/evaluation/results/baseline_v1/`) —
**they are not the current official baseline.**

**Phase 4A.1 — evidence-level correction (official baseline).** The
evaluator was corrected so that success is measured against
`expected_evidence` — a specific (document, discriminant) pair — never a
bare document ID. A result now satisfies an evidence unit only when its
document matches **and** either its exact Rule ID metadata (`rule_ids`)
or a normalized `section_path`/`section_title` cue also matches.
Concretely, the bug was exposed by query `"GOLD-032"`: under the old
methodology, an unrelated `gold_business_rules > Objetivo` chunk (same
document, wrong section, rank 11) counted as a hit; under the corrected
methodology it does not — only `gold_business_rules > Definição de
pedido de compra em aberto` (the chunk that actually carries
`GOLD-032`) counts, and it was **absent from Top-15 entirely** under
plain HYBRID.

**Official corrected baseline** (`rag/evaluation/results/baseline_v1_corrected/`,
same 37-query dataset, evidence-level metrics — this is empirical
baseline data, not a pass/fail threshold):

| K | Hit@K | Evidence Coverage@K | Precision@K | nDCG@K |
|---|---|---|---|---|
| 3 | 0.939 | 0.894 | 0.455 | 0.822 |
| 5 | 0.939 | 0.909 | 0.297 | 0.819 |
| 8 | 0.970 | 0.955 | 0.212 | 0.815 |
| 10 | 0.970 | 0.970 | 0.176 | 0.811 |
| 15 | 0.970 | 0.970 | 0.125 | 0.799 |

MRR = 0.758.

**Key findings:**

- **`BR-03` / `"GOLD-032"`:** under plain HYBRID, the correct evidence is
  absent from Top-15 entirely — not merely low-ranked.
- **`FC-05`:** correct forecasting evidence ("Tratamento de meses sem
  demanda") sits at rank 6 — outside a Top-5 window.
- **Generic headings:** 31 generic-heading occurrences in Top-5 across
  the dataset; 26 of 31 graded irrelevant (grade 0) once evidence-level
  grading replaced document-level grading.
- **Negative/OOD:** 3 of 4 negative queries produced HYBRID top-1 scores
  close to genuine positive-query scores. **Conclusion: retrieval score
  alone is not reliable evidence-sufficiency signal.** No score threshold
  was defined — this remains open (see "Known Limitations" below).

### Identifier Retrieval Experiments (Phase 4B)

A dedicated 14-query dataset of real `GOLD-NNN` identifiers across 4
canonical documents (`rag/evaluation/dataset/identifier_queries_v1.jsonl`)
was evaluated under 4 isolated configurations. **No regression was
observed within this identifier evaluation subset** — these results are
specific to bare-identifier queries, not a general claim about
non-identifier retrieval:

| Experiment | Hit@1 | Hit@5 | Hit@15 | MRR |
|---|---|---|---|---|
| A0 — plain HYBRID (control) | 0.000 | 0.071 | 0.286 | 0.045 |
| A1 — `FULL_TEXT` | 0.071 | 1.000 | 1.000 | 0.443 |
| A2 — HYBRID + `rule_ids` metadata filter | 0.429 | 1.000 | — | **0.631** |
| A3 — deterministic query expansion | 0.000 | 0.214 | 0.286 | 0.068 |

**Conclusion (evidence-backed): plain HYBRID is inappropriate for exact
`GOLD-ID` queries.** The strongest measured route is HYBRID + a `rule_ids`
metadata filter (A2, highest MRR). `FULL_TEXT` (A1) is a validated
alternative, not the primary route. Deterministic query expansion (A3)
showed no meaningful improvement and was rejected from the policy;
its experiment artifacts are preserved for auditability, not deleted.

**Databricks reranker:** tested for availability and found
**`UNAVAILABLE_IN_CURRENT_ENVIRONMENT`** — the query API accepted the
reranker request schema, but the workspace returned: *"the Reranker
feature is enabled in this workspace, however a workspace-level
configuration is preventing us from accessing the reranker model."* This
is a capability limit observed in this workspace, not a finding that
reranking fails as a method or is unnecessary — it remains unevaluated
here, not rejected.

### Retrieval Policy (Phase 4C)

A small, deterministic, testable routing policy
(`rag/src/retrieval/policy.py`) — not an LLM router, not an NLP intent
classifier — decides retrieval behavior **within** the existing AI Search
retrieval path, in this precedence order:

1. **`EXACT_RULE_ID`** — the entire query, once whitespace-trimmed,
   matches `GOLD-\d{3}` case-insensitively and nothing else (e.g.
   `"GOLD-032"`; **not** `"Explique GOLD-032"`). Route: HYBRID + a
   `rule_ids` metadata filter derived only from that same token + Top-K
   = 5 (Phase 4B A2's measured route). A syntactically valid but
   nonexistent Rule ID (e.g. `"GOLD-999"`) still takes this route and
   returns **zero results with no semantic fallback** — an explicit
   identifier lookup must not silently substitute a semantically similar
   but incorrect rule.
2. **`CROSS_SOURCE`** — not an exact identifier, and the query text
   contains an explicit Gold-side cue (`Gold`, excluding a `GOLD-NNN`
   token) **and** an explicit Power BI-side cue (`Power BI`, `dashboard`,
   `modelo semântico`, `semantic model`) together. Route: HYBRID, Top-K =
   10, a single retrieval call (no multi-query split, no reranker).
   Deliberately conservative — a query naming only one side never
   matches, and no semantic/LLM inference is used. **Known limitation:**
   one of the four cross-source evaluation queries (about RFM metrics)
   does not literally name "Gold" and is a disclosed false negative,
   routed as `NORMAL` instead.
3. **`NORMAL`** — everything else (the default). HYBRID, Top-K = 8, no
   metadata filter.

**Top-K rationale (offline analysis, reusing existing Phase 4A.1 Top-15
results — no new retrieval calls):** for the 29 queries classified
`NORMAL`, Hit@K and Evidence Coverage@K both reach 1.000 exactly at K=8
(the one prior miss, `FC-05`, resolves there), with precision continuing
to fall and no further benefit at K=10/15 — so K=8 is the smallest depth
with no known misses, not a max-recall choice. For the 3 queries
classified `CROSS_SOURCE`, Evidence Coverage reaches 1.000 only at K=10
(nDCG@10 equals nDCG@15, so K=15 adds nothing) — **this is derived from
only 3 queries**, a small sample, disclosed as a limitation below.

**Runtime validation (5 live queries, `query → classify_query() →
RetrievalPolicyDecision → AI Search`, no LLM involved):**

| Case | Query | Route | Top-K | Result |
|---|---|---|---|---|
| A | `"GOLD-032"` | `EXACT_RULE_ID` | 5 | correct evidence at rank 4 |
| B | `"GOLD-999"` | `EXACT_RULE_ID` | 5 | 0 results, no fallback |
| C | "Como o Power BI calcula a medida Ticket Médio?" | `NORMAL` | 8 | correct evidence at rank 1 |
| D | "Como o Ticket Médio é definido no Gold e implementado no Power BI?" | `CROSS_SOURCE` | 10 | both evidence units recovered (ranks 1 and 9) |
| E | `FC-05`'s original query | `NORMAL` | 8 | correct evidence at rank 6 |

**Policy-level evaluation** (offline, reusing Phase 4A.1/4B results, no
new calls, truncated to each route's own Top-K): of the 33 positive
(non-negative) evaluation queries — 1 routed `EXACT_RULE_ID`, 3
`CROSS_SOURCE`, 29 `NORMAL` — **zero evidence misses were observed**
(overall MRR = 0.765). The `EXACT_RULE_ID` route itself is backed more
strongly by the separate 14-query Phase 4B identifier experiment than by
this single in-dataset query — `retrieval_queries_v2.jsonl` contains only
one query classified `EXACT_RULE_ID`. **This does not mean retrieval is
100% accurate in general** — it means zero evidence misses were observed
across these specific 33 queries in this specific evaluation dataset;
see "Retrieval Quality — Known Limitations" below.

### Retrieval Quality — Known Limitations

Not blockers for Phase 5A (closed) or Phase 6A (next), but genuinely open:

1. **Reranker unavailable** in the current workspace (see above) —
   unevaluated, not rejected.
2. **Generic-heading pollution remains** — the Phase 4C policy changes
   *which* Top-K each route uses, not *what* gets returned within it; the
   26-of-31 generic-heading-graded-irrelevant finding from Phase 4A.1 is
   unchanged by routing.
3. **Negative/OOD evidence sufficiency is unresolved** — no score
   threshold exists; 3 of 4 negative queries still score close to genuine
   positives.
4. **Cross-source Top-K (10) is derived from only 3 queries** — small
   sample, directionally consistent with the original 4-query
   cross-source finding but not broadly validated.
5. **The conservative cross-source classifier has a known false
   negative** (the RFM cross-source query) — disclosed, not silently
   patched by broadening the cue list.
6. **The retrieval policy is validated only against the current
   canonical corpus and these specific evaluation datasets** — not a
   general guarantee across arbitrary future queries or a larger corpus.

**Still PLANNED, not yet implemented:**

- Asset Bundle / Infrastructure-as-Code representation of
  `document_chunks_index` — it currently exists only because it was created
  directly via the Databricks Vector Search API/CLI, not declared in
  `resources/resources.yml`. See "Phase 3B — AI Search Infrastructure as
  Code" in the Roadmap.
- Reranking, once/if the workspace configuration allows it.
- Generic-heading mitigation (content restructuring) and an
  evidence-sufficiency threshold for OOD queries — both explicitly
  deferred to a future Harness-level concern, not solved by retrieval
  routing alone.
- The Context Manager / token-budget step (see "Top-K vs Token Budget") —
  Phase 5A's orchestrator sends the full retrieved evidence set as context
  without a separate token-budget selection layer; this is now the actual
  behavior, not merely a planned gap (see "Retrieval Policy (Phase 4C)"
  above and "Orchestration Layer — LlamaIndex" below).
- Evidence-sufficiency verification beyond the empty-retrieval guard (see
  "Response Verification / Groundedness") — citation-*label* existence is
  now checked structurally (Phase 6A/8, `check_citation_integrity`: every
  `[Sn]` the model cites is checked against the real `sources` metadata),
  but whether the cited source actually, semantically supports the claim
  next to it (groundedness) is still not verified. The current retrieval
  policy (Phase 4C) decides behavior **within** AI
  Search retrieval only; it is a distinct, narrower concern from the
  Harness's routing between AI Search and the structured Delta/SQL data
  path (see "Structured Runtime Data — Analytical / ML Outputs (PLANNED)")
  — the Harness itself exists (Phase 6A), and since Phase 6B.1 it decides
  between `AI_SEARCH`/`STRUCTURED_DATA`/`BOTH` with a deterministic
  rule-based router (implemented and unit-tested locally, not live until
  Phase 6B.2 — see "Structured Data & Source Routing"). That source-level
  routing is not what Phase 4C's policy, or the Phase 5A orchestrator,
  does.

## Structured Runtime Data — Analytical / ML Outputs (PLANNED)

Canonical knowledge and dynamic analytical data use **different access
paths**. This is an architectural decision. As of Phase 6B.1 it is
implemented locally as generic Structured Analytics over the Gold layer (see
"Structured Data & Source Routing"); the ML / forecast outputs described in this section
remain PLANNED.

### Stable knowledge

Examples: business rules, metric definitions, forecasting methodology,
dashboard/semantic-model rules, architecture, interpretation/governance
guidance.

Flow (this is the existing, runtime-validated Knowledge/Data + Retrieval
path — see "Architecture — Current, Implemented" and "Retrieval
Architecture" above):

```
canonical Markdown
        |
        v
   document_chunks
        |
        v
Databricks AI Search
        |
        v
   Hybrid Search
```

### Dynamic structured data (PLANNED)

Examples: ML predictions, current forecasts, actual demand, stock values,
stockout-risk scores, model output values, comparisons between legacy
forecasting and ML, model version / prediction timestamp.

Flow (not implemented today):

```
Delta table / view
        |
        v
parameterized structured query
        |
        v
   runtime evidence
```

These values must **not** be:

- converted into Markdown on every model run;
- chunked;
- embedded;
- written into `document_chunks`;
- synchronized into the canonical AI Search index.

`document_chunks` / `document_chunks_index` remain reserved for the five
stable canonical documents (see "Canonical Knowledge Base"); dynamic
analytical/ML values never flow through that path.

### Example hybrid question

> "Qual era a projeção pelo método antigo comparada ao modelo de ML?"

Answering this may require, simultaneously:

- **A. AI Search** — to retrieve method definitions, business rules,
  methodology, interpretation.
- **B. Delta / SQL** — to retrieve actual values: period, product,
  baseline/legacy method, ML prediction, model version.

```
structured evidence + documentary evidence
        |
        v
    orchestration
        |
        v
       Llama
        |
        v
grounded natural-language response
```

The generation model must not invent or recompute prediction values that
did not come from the structured source.

### Conceptual fields (OPEN — no schema defined)

The ML pipeline producing these outputs is not finalized. No physical
table/view name, and no closed schema, is defined here. Conceptually
possible fields, marked **CONCEPTUAL / OPEN**, not an implemented
contract:

- `product_id` / relevant business key
- `forecast_date` / forecast period
- legacy/baseline prediction
- `ml_prediction`
- actual value, when available
- `model_version`
- `prediction_timestamp`

No such table or view currently exists. This is intentionally left open
until the ML pipeline itself is finalized.

### "ML outputs" vs MLflow — distinct concepts

These must not be conflated:

- **ML outputs / predictive ML** — business/model prediction data,
  stored and queried through Delta/SQL, as described above.
- **MLflow** — the Quality / Evaluation / Observability plane for the
  RAG/application itself (tracing, evaluation harness — see "Quality,
  Evaluation and Observability (MLflow)"). MLflow remains transversal to
  the six-layer architecture (see "Final Architecture — Six Layers"); it
  is not the mechanism by which ML prediction values reach the
  application.

### Relationship to the Adriano POC

The project has an existing, useful **end-to-end proof-of-concept /
implementation reference**, built by Adriano Takata, demonstrating:

```
PDF -> extraction -> chunking -> Delta -> Vector Search -> Hybrid Search -> Llama generation
```

Parts of it are genuinely useful as an implementation reference for this
project's later phases:

- how to use `VectorSearchClient`;
- how to create/use an AI Search (Vector Search) index;
- Hybrid Search configuration;
- how to call Databricks-hosted Llama.

The following are **not** incorporated into the final architecture,
deliberately:

- its PDF extraction pipeline (this project's document-source pipeline is
  a separate, not-yet-built design — see "Future Document Preprocessing
  Pipeline");
- its fixed-size `RecursiveCharacterTextSplitter` (1200/200) chunking —
  this project's chunking is structure-aware and closed (see "Chunking
  Strategy v1");
- its table/index (`rag.chunks`, `chunks_index` in Adriano's own dev
  catalog) — not a dependency;
- its overwrite-based / incremental notebook pattern (`processed_files`
  tracking + `DELETE` + `APPEND`) — this project's persistence is a single
  atomic scoped `MERGE` (see "Persistence Layer"), never `DELETE`+`APPEND`;
- `databricks-gte-large-en` as a default embedding model — English-only,
  not adopted (see "Embedding Model — MVP").

One concrete design distinction worth preserving explicitly: the Adriano
POC's incremental behavior operates primarily at the **file** level
(`processed_files` + hash tracking), while this project's ingestion is
incremental/change-aware at the **chunk** level, through deterministic
chunk identity and the scoped MERGE described in "Document and Chunk
Change Control" above. The notebook titled "Rag Incremental" specifically
is **not** part of this project's final architecture.

**Current state:** this project no longer merely plans its own index — it
has its own runtime-validated `document_chunks_index`, on the shared
`rag_endpoint`, built on `document_chunks` only, with `databricks-qwen3-
embedding-0-6b` managed embeddings and Hybrid Search smoke-tested (see
"Retrieval Architecture" above). The distinctions listed above remain
accurate: none of the Adriano POC's PDF pipeline, splitter, table/index,
or `DELETE`+`APPEND` pattern were adopted in building it.

## Orchestration Layer — LlamaIndex

**IMPLEMENTED / RUNTIME VALIDATED (Phase 5A — `ORCHESTRATION_RUNTIME_VALIDATED`).**
**LlamaIndex is the orchestration framework, not the architecture, and not
the policy.** It is the Orchestration Layer (Layer 4) at runtime,
connecting the Retrieval Layer (AI Search, fronted by the Phase 4C
Retrieval Policy) and the Generation Layer (Databricks-hosted Llama — see
"Generation Design"). The end-to-end path has been exercised for real:

```
validated Retrieval Policy (Phase 4C)
        |
        v
existing Databricks AI Search
        |
        v
LlamaIndex orchestration (orchestration/rag_pipeline.py)
        |
        v
Databricks-hosted Llama (generation/llama_client.py)
        |
        v
grounded response + deterministic source metadata
```

`ORCHESTRATION_RUNTIME_VALIDATED` means exactly this technical path works
end to end — it does **not** mean answer quality has been formally
evaluated, hallucination is solved, evidence sufficiency is solved,
citation integrity is solved, the AI Harness is implemented, MLflow is
implemented, or structured/ML data is integrated. See "Known Generation
Limitations" below.

**LlamaIndex does not re-decide retrieval.** The Phase 4C Retrieval
Policy remains authoritative: LlamaIndex never classifies the query,
never picks `query_type`/Top-K, never builds a `rule_ids` filter, and
never re-runs retrieval on its own. Conceptually:

```
query
  |
  v
Retrieval Policy (classify_query)
  |
  v
AI Search (existing client, unchanged)
  |
  v
retrieved evidence
  |
  v
LlamaIndex orchestration (translation, context assembly)
  |
  v
Llama
```

**Custom retriever, not the official Databricks Vector Search
integration.** LlamaIndex ships an official `DatabricksVectorSearch`
adapter, but this project uses a small custom `BaseRetriever` subclass
(`PolicyRetriever`, in `orchestration/rag_pipeline.py`) wrapping the
existing, already-validated `src/retrieval/ai_search.py` client instead.
This is not a criticism of the official integration — it was not adopted
because doing so would introduce a second query path that could diverge
from the already-validated application-specific retrieval semantics
(HYBRID behavior, the `rule_ids` metadata filter, route-specific Top-K,
and the exact metadata fields returned); the custom adapter guarantees
those semantics are preserved exactly because it calls the same client
function every other retrieval code path in this repository calls.

The project does **not** use
`VectorStoreIndex.from_documents(...)` — LlamaIndex is never used to
recreate ingestion, re-embed documents, or create a second Vector Search
/ Direct Access index. Ingestion remains this project's own closed
pipeline (Layer 1): canonical Markdown → custom loader → custom parser →
custom chunker → Delta `document_chunks` → Databricks AI Search. LlamaIndex
enters only **after** retrieval.

**What LlamaIndex does here:**

- executes a pre-computed `RetrievalPolicyDecision` through the existing
  AI Search client and translates the raw results into LlamaIndex nodes
  (`PolicyRetriever`);
- assembles a deterministic, source-labeled (`S1`, `S2`, ...) grounding
  context and prompt (`build_context_blocks`, `GROUNDING_PROMPT_TEMPLATE`);
- calls the Databricks Llama model through a `CustomLLM` adapter
  (`DatabricksLlamaLLM`, in `generation/llama_client.py` — see "Generation
  Design");
- returns an `OrchestrationResult` — answer, evidence availability,
  deterministic sources, the `RetrievalPolicyDecision` that was used,
  model name, and retrieval/generation/total latency (plus token usage
  when the API returns it).

**What LlamaIndex does NOT do here** (owned by the AI Harness, Phase 6A —
see "AI Harness" — or, where noted, still not implemented anywhere):

- decide AI Search vs. structured/Delta/SQL data, or fetch structured
  evidence — the Harness does (Phase 6B.1, see "Structured Data & Source
  Routing"); `rag_pipeline.py` never sees structured values and contains no
  SQL. For a `BOTH` question it only writes the documentary explanation;
- score or gate evidence sufficiency beyond the empty-retrieval guard —
  not implemented anywhere yet;
- validate citations — **implemented**, but in `evaluation/citations.py`,
  called from the App, not inside LlamaIndex or `rag_pipeline.py`;
- apply guardrails beyond the empty-retrieval guard — not implemented
  anywhere yet;
- log to MLflow — **implemented**, but from `harness/tracing.py`,
  external to both LlamaIndex and `rag_pipeline.py`.

LlamaIndex does **not** replace any of this project's own closed
engineering, and does **not** become a new source of truth — none of the
following are, or will be, delegated to it: `loader.py`/`parser.py`/
`chunker.py`; deterministic hashing/chunk IDs; Delta persistence/atomic
`MERGE`; provenance; document lifecycle; quality gates for future external
documents; context/token budgeting (still not implemented anywhere, see
"Top-K vs Token Budget"); evidence/citation validation (implemented, but
in `evaluation/citations.py` and the App layer, not in LlamaIndex);
creating or computing ML predictions.

### Current lean structure (`orchestration/rag_pipeline.py`)

Phase 5A initially produced six separate files under `orchestration/`
(`evidence.py`, `retriever_adapter.py`, `context.py`, `structured_data.py`,
`llm_adapter.py`, `orchestrator.py`). **This is historical only** — an
"Architecture Scope Review — Lean RAG Core" (see "Roadmap") inspected real
usage and found zero external consumers of any of those modules outside
the package itself, and consolidated them:

```
rag/src/
├── retrieval/
│   ├── ai_search.py
│   ├── policy.py
│   └── query_routing.py
├── orchestration/
│   ├── __init__.py
│   └── rag_pipeline.py        <- RetrievedEvidence, PolicyRetriever,
│                                  context formatting, OrchestrationResult,
│                                  RagOrchestrator — one cohesive module
├── generation/
│   ├── __init__.py
│   ├── llama_client.py        <- DatabricksLlamaLLM
│   ├── claude_client.py       <- 0-byte legacy placeholder, INACTIVE
│   └── search_results.py      <- 0-byte legacy placeholder, INACTIVE
└── evaluation/
    └── ...
```

For the current project size (6 canonical documents, one AI Search index,
one generation model, no structured-data runtime, no Harness, no MLflow,
no App), one cohesive `rag_pipeline.py` is an intentional design choice,
not an unrefactored monolith: `evidence.py`, `retriever_adapter.py`,
`context.py`, and `orchestrator.py` formed a strictly linear chain
(evidence → retriever adapter → context → orchestrator) with no
independent reuse anywhere in the codebase to justify separate files.
`llm_adapter.py` was moved (not merged) to `generation/llama_client.py`,
since "how the model is called" is a generation-layer concern, not an
orchestration one — `rag_pipeline.py` depends only on the plain
LlamaIndex `CustomLLM` interface and has no Databricks-specific
knowledge. The current, lean modular-monolith organization:

```
retrieval/       -> how evidence is found
orchestration/    -> how one RAG request is coordinated
generation/       -> how the model is called
evaluation/       -> how quality is measured
```

**Structured-data decision:** `structured_data.py` (a `StructuredEvidence`
dataclass + a `StructuredDataProvider` `Protocol`, with zero
implementations and zero runtime callers) was deliberately **removed**
during the Lean Core review, not merely deferred in place. There is
currently no real Delta/SQL provider, no ML output contract, and no final
prediction schema — keeping a speculative, unimplemented interface on the
theory that a future Harness might need it did not meet this project's
bar for a separate abstraction. This does **not** mean structured data is
abandoned: the future path remains

```
ML / analytical pipeline
        |
        v
Delta / SQL output
        |
        v
structured query service (Phase 6B.1 and generic Structured Analytics implemented;
ML/forecast outputs not yet designed)
        |
        v
AI Harness source routing (Phase 6B.1)
        |
        v
RAG orchestration
```

and a concrete implementation was introduced in Phase 6B.1 and generalized to
approved metrics from the Power BI semantic model, under `src/structured/` (the removed
placeholder here was not resurrected — see "Structured Data & Source
Routing"); ML/forecast outputs still need a real data contract (see
"Structured Runtime Data — Analytical / ML Outputs (PLANNED)" and "Phase
6B" in the Roadmap) — no physical schema is invented here.

### Runtime validation (5 live smoke calls, Phase 5A)

| Case | Query | Route | Top-K | Evidence | Generation |
|---|---|---|---|---|---|
| A | `"GOLD-032"` | `EXACT_RULE_ID` | 5 | correct evidence at rank 4 | grounded answer, cites `[S4]` |
| B | `"GOLD-999"` | `EXACT_RULE_ID` | 5 | 0 results | **no model call** (empty-retrieval guard) |
| C | "Como o Power BI calcula a medida Ticket Médio?" | `NORMAL` | 8 | correct evidence at rank 1 | grounded answer succeeded |
| D | "Como o Ticket Médio é definido no Gold e implementado no Power BI?" | `CROSS_SOURCE` | 10 | Power BI evidence rank 1, Gold metric evidence rank 9 | correctly distinguishes the two sources, does not collapse them into one undocumented rule |
| E | `FC-05`'s original query | `NORMAL` | 8 | correct evidence at rank 6 | grounded answer succeeded |

**Known observation from Case C:** the generated answer contained one
attribution claim not strictly supported by the retrieved evidence blocks
shown to the model. This is a concrete, observed example of why claim-
level (semantic) validation, not just citation-*label* validation, matters
— the Phase 6A/8 citation checker (`check_citation_integrity`, see "AI
Harness") would not have caught this specific case, because the model's
`[Sn]` label was presumably still a real, existing source; what was wrong
is that the label doesn't strictly support the specific claim next to it,
which is a semantic-groundedness question the current structural checker
explicitly does not answer (see "Response Verification / Groundedness").
Phase 5A's deterministic `sources` metadata is correct and independent of
this regardless.

**Empty-retrieval behavior (validated):** for Case B (`"GOLD-999"`) the
retrieval policy still routes `EXACT_RULE_ID` and the `rule_ids` filter
still runs, but returns 0 results. The orchestrator's response is
deterministic: `evidence_available = false`, `answer = null`,
`sources = []`, and **no model call is made**. This is a deterministic
empty-retrieval guard, not complete evidence-sufficiency handling — it
only covers the "zero candidates returned" case, not the "candidates
exist but are insufficient/irrelevant" case (see the OOD diagnostic
below and "Response Verification / Groundedness").

**OOD diagnostic (informational only, not a success gate):** one
out-of-distribution query with an irrelevant but high-scoring retrieved
neighbor (top-1 score 0.955) was answered by the model with an
insufficient-evidence response ("não há evidência suficiente..."),
because the grounding prompt instructs it to say so rather than guess.
This is an encouraging single diagnostic result, **not** evidence that
OOD rejection is solved — there is still no deterministic
evidence-sufficiency mechanism, and Phase 4A.1/4B already showed 3 of 4
negative queries score close to genuine positives at the retrieval level.

### Known Generation Limitations

Inputs to later phases, not Phase 5A failures:

1. Evidence sufficiency is not formally implemented — only the
   deterministic empty-retrieval guard exists.
2. Citation validation is not enforced — the model's `[Sn]` markers are
   not checked against the deterministic `sources` metadata (see Case C
   above).
3. OOD rejection is not solved — one favorable diagnostic result is not a
   mechanism.
4. The Databricks reranker remains unavailable in the current workspace
   (unchanged from Phase 4B).
5. Structured ML/forecast data integration is not implemented; one
   Gold-layer analytical intent exists locally since Phase 6B.1, not live
   (see "Structured Data & Source Routing").
6. Answer-quality evaluation is not yet formal — no LLM-judge, no MLflow.
7. The generation client's `max_output_tokens` budget (512 by default) can
   truncate longer grounded answers, as observed in Case C above.

### Lean refactor behavioral validation

The Architecture Scope Review's consolidation (files moved/merged, none
of the logic rewritten) was verified to be behavior-preserving, both by
the full unit-test suite and by one live re-run after the refactor:

- `"GOLD-032"` → `EXACT_RULE_ID` → HYBRID + `rule_ids` filter / Top-K=5 →
  grounded answer — unchanged.
- `"GOLD-999"` → 0 evidence → no model call — unchanged.
- `NORMAL` Top-K=8, `CROSS_SOURCE` Top-K=10 — unchanged.
- Deterministic `S1`, `S2`, ... source labeling — unchanged.
- Same Llama endpoint, same model defaults (`temperature=0.0`,
  `max_output_tokens=512`), same OAuth/profile-based authentication —
  unchanged.
- No retrieval reclassification was introduced inside LlamaIndex by the
  refactor.

## Generation Design

**IMPLEMENTED / RUNTIME VALIDATED (Phase 5A).** Databricks-hosted Llama
via the Foundation Model API, using pay-per-token billing (see "Databricks
Free Edition Constraints"). This is the Generation Layer (Layer 3) of the
architecture.

**Model:** `databricks-meta-llama-3-3-70b-instruct`. Confirmed `READY` in
this workspace (`databricks serving-endpoints list`) before use — not
inferred from public documentation. A direct capability smoke call (no
project data, trivial prompt) succeeded: latency ≈ 1.08 s, `prompt_tokens
= 40`, `completion_tokens = 2`, `total_tokens = 42`. These numbers describe
that one capability check only — they are **not** a general performance
benchmark; see "Runtime validation" above for actual grounded-generation
latencies (typically 2.5–5 s per call, evidence-dependent).

**Client:** `rag/src/generation/llama_client.py`, class
`DatabricksLlamaLLM` — a LlamaIndex `CustomLLM`. Responsibilities:

- model invocation against the Foundation Model API serving endpoint;
- authentication via the existing Databricks SDK `WorkspaceClient` /
  `~/.databrickscfg` profile mechanism (OAuth-compatible, U2M) — the same
  authenticated client every other Databricks-facing module in this repo
  uses. **No static bearer token, and no secret, is stored in this
  repository.** The official `llama-index-llms-databricks` package was
  evaluated and not adopted for the runtime path, specifically because it
  requires a static `api_key`/`DATABRICKS_TOKEN`-style bearer token, which
  this project's OAuth-based profile does not provide without building a
  second, parallel auth path next to the SDK's own;
- LlamaIndex `CustomLLM` compatibility (`complete`/`chat`, `metadata`,
  explicit `NotImplementedError` on `stream_complete`/`stream_chat` —
  streaming is out of scope);
- token-usage extraction when the API returns it (never estimated when it
  does not);
- centralized model configuration: `max_output_tokens = 512`,
  `temperature = 0.0` by default.

**Model-agnostic boundary, kept small:** the rest of the application
(orchestration, and eventually the Harness) depends only on the plain
LlamaIndex `LLM`/`CustomLLM` interface, never on `DatabricksLlamaLLM`
directly by name outside `generation/` and its construction site — so a
different Databricks-hosted model could be swapped in later without
touching retrieval, orchestration, or evaluation code. This is deliberately
**not** a multi-vendor abstraction: no Claude-specific compatibility, no
provider routing, no provider marketplace. `generation/claude_client.py`
and `generation/search_results.py` remain **0-byte legacy placeholders,
inactive** — Claude is not, and has not been, part of the active
architecture (see "Current Architectural Decisions").

## AI Harness (Phase 6A — IMPLEMENTED for `AI_SEARCH`; Phase 6B.1 adds local structured routing, NOT LIVE)

The target system treats the generation model as only **one component** of
the RAG application, not the whole application. The core control layer
described in this section is now implemented and sits above the layer it
was designed for (Retrieval Policy → AI Search → LlamaIndex orchestration
→ Databricks Llama — see "Orchestration Layer — LlamaIndex"). Phase 6A
scoped it deliberately to the `AI_SEARCH` route; Phase 6B.1 added the
`STRUCTURED_DATA` and `BOTH` routes locally (see "Structured Data & Source
Routing" — implemented and unit-tested, **not live**). The `AI_SEARCH`
runtime boundary is:

```
AI Harness
   |
   v
rag_pipeline.py (RagOrchestrator.generate)
   |
   v
retrieval + generation
```

The Phase 4C retrieval policy (`EXACT_RULE_ID`/`CROSS_SOURCE`/`NORMAL`
routing, see "Retrieval Architecture") is retrieval-layer logic that
decides behavior **within** AI Search — it is not the Harness's
source-routing (which decides *between* `AI_SEARCH`, `STRUCTURED_DATA`, or
`BOTH`, via `SourceRoute`). These are different levels of routing and are
not merged: Phase 4C's policy is called automatically by
`RagOrchestrator.generate`, unchanged; `AIHarness.ask` calls
`RagOrchestrator.generate` and never reimplements, duplicates, or bypasses
it.

The **full target** responsibility set (below) explicitly includes source
routing between documentary retrieval and a structured analytical query —
the `AI_SEARCH` branch is live-validated; the structured branch is
implemented locally (Phase 6B.1) and not live:

```
User Request
      |
      v
+-----------------------------------+
|            AI HARNESS             |
|                                    |
| request validation      [DONE]    |
| intent / source-routing policy    |
|   (AI Search vs structured data   |
|    vs both)   [DONE locally, 6B.1:|
|          deterministic rule-based |
|          router; not live]        |
| context / evidence assembly       |
| token budget           [PLANNED]  |
| model invocation policy [DONE, via|
|                      RagOrchestrator]
| response validation    [PARTIAL]  |
| citation / evidence verification  |
|            [DONE, evaluation/     |
|             citations.py, called  |
|             by the App — see below]
| tracing / evaluation hooks        |
|      [tracing: DONE, harness/     |
|       tracing.py. evaluation: PLANNED]
+----------------+-------------------+
                 |
                 v
     rag_pipeline.py (RagOrchestrator)
     [IMPLEMENTED, Phase 5A — LlamaIndex
      orchestration; see "Orchestration Layer"]
           /        |        \
          v         v         v
     AI Search  Delta/SQL   Llama
    [IMPLEMENTED] [LOCAL,   [IMPLEMENTED]
                  6B.1, not
                  live]
          |          |
          v          v
  document_chunks  predictions /
                    analytical views
          |
          v
    Unity Catalog

                    MLflow (tracing implemented,
                     see harness/tracing.py — see
                     "Quality, Evaluation and Observability")
```

Actual module structure (differs from an earlier plan — see below):

```
src/
└── harness/
    ├── harness.py     <- SourceRoute, HarnessStatus, HarnessResult,
    │                     AIHarness.ask() — request validation, source
    │                     routing container, controlled-failure containment
    └── tracing.py      <- enable_autolog(), traced_ask() — MLflow
                            parent-span wrapper, called from OUTSIDE
                            AIHarness (never imported by harness.py)
```

Responsibilities, as actually implemented:

- **`harness.py` (`AIHarness.ask`)** — validates the query (non-empty,
  non-whitespace) before touching any route, returning `INVALID_REQUEST`
  without ever calling `RagOrchestrator` or a structured-data provider;
  resolves `source_route` (with an injected `SourceRouter` — the App injects
  the deterministic `RuleBasedSourceRouter` since Phase 6B.1 — from the
  query text; without one it defaults to `AI_SEARCH`, the original Phase 6A
  behavior; the route is never accepted from the request body); for `AI_SEARCH`, calls
  `RagOrchestrator.generate` unchanged and maps its outcome to
  `AI_SEARCH_OK` / `AI_SEARCH_NO_EVIDENCE`; catches any exception from
  that call and returns it as a controlled `RAG_EXECUTION_FAILED` result
  instead of propagating it. `STRUCTURED_DATA`/`BOTH` are handled by
  `StructuredQueryService` (Phase 6B.1, see "Structured Data & Source
  Routing"; new statuses `STRUCTURED_DATA_OK`, `BOTH_OK`, `BOTH_DEGRADED`,
  `STRUCTURED_DATA_NO_DATA`, `STRUCTURED_DATA_FAILED`,
  `STRUCTURED_DATA_UNSUPPORTED`). The original placeholder statuses
  (`STRUCTURED_DATA_UNAVAILABLE` — still used when a structured route is
  selected but no service is configured, which is what would happen if the
  6B.1 code were deployed before the Phase 6B.2 configuration —,
  `STRUCTURED_DATA_FETCHED_NO_GENERATION`,
  `BOTH_PARTIAL`) and the legacy `StructuredEvidence`/
  `StructuredDataProvider` interface are retained only for the pre-6B.1
  Harness tests: no production code builds a provider, and cleanup is
  deferred. `harness.py` also re-declares a minimal, deliberately generic
  `StructuredEvidence`/`StructuredDataProvider` interface (no
  table/catalog/schema/SQL detail) — this is **not** a contradiction of
  the Lean Core review's removal of orchestration's own
  `structured_data.py`: that removal was about a zero-caller placeholder
  sitting inside `rag_pipeline.py`'s package; this one exists specifically
  so `AIHarness`'s constructor and routing logic have a typed shape to
  route *toward*, with zero implementations of it anywhere in the
  repository, same as before.
- **`tracing.py` (`enable_autolog`, `traced_ask`)** — deliberately **not**
  part of `harness.py`, so `AIHarness` stays fully observability-framework
  agnostic (no `import mlflow` anywhere in that file). `enable_autolog()`
  turns on `mlflow.llama_index.autolog()` once per process, which already
  captures `PolicyRetriever.retrieve`/`_retrieve` and
  `DatabricksLlamaLLM.complete` as spans with no manual instrumentation.
  `traced_ask()` is a thin `@mlflow.trace`-decorated wrapper around
  `AIHarness.ask` that adds exactly one thing autolog cannot know on its
  own: one parent span (so the two autologged spans nest under one request
  trace instead of two disconnected ones) plus a handful of request-level
  attributes (`SourceRoute`, `HarnessStatus`, the resolved
  `RetrievalRoute`/`top_k`, evidence count). It changes nothing about
  Harness behavior — it only observes.
- **Citation validation** — implemented, but lives in
  `evaluation/citations.py` (`check_citation_integrity`), not in
  `harness.py` — it is called from the Databricks App (`app.py`), not from
  the Harness itself (see "Interface Layer — Databricks App" and "Response
  Verification / Groundedness"). This keeps `AIHarness` focused on
  routing/validation/failure-containment only.
- **Context/token budget, general evidence-sufficiency judgment, and
  structured-data guardrails** — **not implemented.** `RagOrchestrator`
  still sends the full retrieved evidence set as context (see "Top-K vs
  Token Budget", still open); the only sufficiency signal anywhere in the
  system remains Phase 5A's narrow empty-retrieval guard.

The originally planned four-file split (`orchestrator.py`,
`context_manager.py`, `guardrails.py`, `tracing.py`) was **not** what got
built: the actual implementation is two files (`harness.py`, `tracing.py`)
plus reuse of an existing evaluation-layer function for citation checking,
because a thin `AIHarness.ask` plus an external tracing wrapper covered
the `AI_SEARCH`-only scope without the extra module boundaries a
not-yet-real structured-data path would have justified. `rag_pipeline.py`
(LlamaIndex-based orchestration) remains the layer the Harness invokes,
unchanged; business rules, governance, and validation policy are still
never delegated to LlamaIndex or `rag_pipeline.py` (see "Orchestration
Layer — LlamaIndex").

## Structured Data & Source Routing (Phase 6B.1 + Structured Analytics — IMPLEMENTED LOCALLY, NOT LIVE)

**Status: implemented and unit-tested locally with fakes. Not live.** No
code in this phase was executed against a real SQL Warehouse from the App:
the currently deployed App does not contain this code, and it has no
`sql_warehouse` resource, no `SQL_WAREHOUSE_ID`, and no structured-data Unity
Catalog grants for its service principal beyond those listed under "What
remains for Phase 6B.2". Those belong to **Phase 6B.2** (live validation),
together with latency and production behavior — nothing about them is claimed
here. The generated SQL has been compiled to strings and checked against the
dbt Gold YAML, but it has never run on a Warehouse.

```
POST /api/ask {"query": "..."}
      |
      v
 AIHarness.ask
      |
      v
 RuleBasedSourceRouter  (deterministic, no LLM)
      |
      +-- AI_SEARCH -------> RagOrchestrator.generate        (unchanged, live)
      |
      +-- STRUCTURED_DATA -> parser -> AnalyticalRequest -> compiler
      |                      -> DatabricksSqlExecutor -> deterministic renderer
      |                                                     (no model call)
      +-- BOTH ------------> the same structured path
                             + RagOrchestrator.generate (documentary
                               explanation only)
```

### Structured Analytics: metric + operation + dimension + filters

Structured data is **generic**, not one intent per question. A question is
mapped to one typed `AnalyticalRequest` — an **approved metric**, an
**operation**, approved **dimensions**, approved **filters**, a time grain or
modifier, and a bounded `top_n` — and a deterministic compiler turns that
request into SQL:

```
natural language
  -> AnalyticalRequest      (enums + bounded integer + dates; no SQL text)
  -> registry validation    (compatibility matrix; fail-closed)
  -> SQL compiler           (allowlisted tables/columns, named parameters only)
  -> DatabricksSqlExecutor  (unchanged)
  -> deterministic renderer (no model reproduces a value)
```

**Power BI is the semantic contract; Gold is the runtime data source.** Every
metric in the registry (`src/structured/semantic_model.py`) names the exact
measure of the "Dashboard Gerencial Compass 360 - LH Nautical" model it
mirrors (documented in `knowledge_base/powerbi_semantic_rules.md`); tests
check every registered table and column against the dbt Gold YAML and every
measure name against that document. The Power BI file is never used at
runtime. There is a single structured intent, `ANALYTICS`; the Phase 6B.1
intent `TOP_PRODUCTS_BY_QUANTITY` is kept for compatibility and is just the
equivalent ranking request, compiled by the same code (its statement is
byte-identical to the one validated against Gold in Phase 6B.0, apart from a
CTE name). There is **no model-generated SQL**: no natural-language string, and
no model output, ever becomes SQL text.

**Operations:** `AGGREGATE`, `RANKING` (top N), `BREAKDOWN` (one dimension),
`TREND` (day/month/quarter/year).

**Metrics (15).**

| Metric | Power BI measure / rule | Gold |
|---|---|---|
| `NET_REVENUE` | Receita Líquida | `fct_sales` (effective sales) |
| `TOTAL_QUANTITY_SOLD` | Quantidade Total Vendida | `fct_sales` |
| `EFFECTIVE_ORDERS` | Pedidos Efetivos | `fct_sales` |
| `AVERAGE_TICKET` | Ticket Médio (one row per order; `order_total_amount` is order-level) | `fct_sales` |
| `RETURN_AMOUNT` | Valor Total Reembolsado | `fct_sales` |
| `RETURN_RATE` | Taxa de Devolução | `fct_sales` |
| `STOCK_ON_HAND` | Estoque Atual | `fct_inventory_replenishment` (snapshot) |
| `DAYS_TO_STOCKOUT` | Dias até Ruptura | `fct_inventory_replenishment` (snapshot) |
| `REPLENISHMENT_REQUIRED` | Itens em Reposição (%) | `fct_inventory_replenishment` (snapshot) |
| `STOCKOUT_RISK` | Itens em Risco de Ruptura | `fct_inventory_replenishment` (snapshot) |
| `INVENTORY_ITEMS` | Itens por Status de Saúde (the four status measures are its health filter) | `fct_current_inventory` (snapshot) |
| `CUSTOMER_COUNT` | Total Clientes | `agg_customer_rfm` (snapshot) |
| `AT_RISK_CUSTOMERS` | Clientes em Risco | `agg_customer_rfm` (snapshot) |
| `AT_RISK_REVENUE` | Receita em Risco (Churn) | `agg_customer_rfm` (snapshot) |
| `AVERAGE_RECENCY` | Recência Média (P1; added only to show a metric is a registry entry) | `agg_customer_rfm` (snapshot) |

Ratios (`DIVIDE` in Power BI) show the percentage together with its numerator
and denominator. `YTD`, `LTM` and `YOY` are **modifiers** of `NET_REVENUE`
(Power BI `TOTALYTD`, `DATESINPERIOD(..., -12, MONTH)`, `SAMEPERIODLASTYEAR`),
not separate metrics.

**Dimensions (9).** `PRODUCT` (product grain: variants folded into
`product_id`), `CATEGORY`, `LOCATION`, `LOCATION_STATE`, `CUSTOMER_STATE`,
`CHANNEL`, `SALESPERSON`, `CUSTOMER_SEGMENT`, `INVENTORY_HEALTH_STATUS`. "State"
is never ambiguous: it is either the establishment's state or the customer's,
and a question that does not say which is refused. **Filters:** a date range
(sales metrics), category, customer segment and inventory health status.

**Power BI semantics that are preserved exactly.**

- **Effective sales only** (`is_effective_sale = TRUE`) on every sales metric.
- **Ranking ties:** products/categories use dense ranking (`RANKX(..., Dense)`);
  the salesperson ranking uses default `RANKX` (ties skip positions). A
  ranking filters on `ranking <= :top_n`, never `LIMIT`, so ties are kept.
- **The champion product is the top product by net revenue** ("produto
  campeão", "campeão de vendas" → `RANKING` + `NET_REVENUE` + `PRODUCT`, top 1).
  "Produto mais vendido" and "mais unidades vendidas" remain quantity rankings.
- **YTD / LTM / YoY need an explicit period.** Power BI evaluates them against
  the report's date context (LTM and %YoY are `BLANK` without one), and the
  Harness has none: without a period they are refused, and **no period is
  ever assumed** (not "today", not "the latest sale"). YTD and LTM are anchored
  on the *end* of the period given (the start is replaced by the window, as in
  the DAX); YoY compares the period with the same period of the previous year.
- **Salesperson ranking.** The measure `Ranking de Vendedores por Receita`
  has no role filter of its own; the report's ranking table filters
  `employee_role IN ('Salesperson')` and `employee_full_name NOT IN (null)`,
  which is the ranking the dashboard shows. The same two filters are applied
  (inner join to `dim_employees`) and the answer states "Considera apenas
  funcionários com cargo Salesperson." The `'Salesperson'` value is taken
  from that Power BI filter; it has not been read from Gold data (no SQL has
  been run), so it is one of the things Phase 6B.2 confirms — a wrong value
  would yield an empty result, never wrong numbers.
- `DAYS_TO_STOCKOUT` is Power BI's `SUM(estimated_days_until_stockout)` (only
  meaningful per item, so it has no plain aggregate), soonest first.

**Fail-closed compatibility matrix** (encoded as data on the registry, not as
scattered `if`s). An invalid combination returns the controlled "unsupported"
outcome and nothing is compiled or executed:

- average ticket is order-level: no product/category breakdown or category filter;
- inventory and RFM metrics are current snapshots: no date filter, trend or
  time modifier;
- products and salespeople can be ranked but not listed as a breakdown;
- ranking/breakdown need exactly one dimension; time modifiers are a single
  aggregate value;
- personal data (email, telephone, address, documents) is never registered
  and questions asking for it are refused;
- "menos vendidos" / ascending rankings, customer rankings, forecasts,
  profit/margin, relative periods ("no último mês", "ontem"), a month without a
  year, filters on a specific store/state/category/product, and rankings by two
  dimensions are refused.

Relative to Phase 6B.1, a period (`em 2024`), revenue ("geraram mais receita"),
rankings by store and totals are now **supported**; "menos vendidos" and
free-form filters stay unsupported.

**Source router and parser.** The router (`harness/source_router.py`) only
decides *which source family* a question needs; the Phase 4C retrieval policy
(`EXACT_RULE_ID`/`NORMAL`/`CROSS_SOURCE`) still decides *how* AI Search
retrieves and is untouched. It splits a question into a documentary clause
("como é calculado", "qual a regra") and a data clause, and hands the data
clause to the parser (`structured/parser.py`) — one small, deterministic,
vocabulary-driven mapper, not one regex family per question. It can only
select approved parts; every request it builds passes the compatibility
matrix. Period words inside the rule clause never affect the data request.
It is a deterministic set of Portuguese cues, not a semantic classifier: a
data question phrased outside its vocabulary falls through to `AI_SEARCH`
(for example "Quais produtos são mais populares?" — a documented, safe false
negative that runs no SQL). Questions that clearly ask for live values the
system cannot produce **fail closed** (`STRUCTURED_DATA_UNSUPPORTED`) instead
of being answered with documentation.

**Deterministic, friendly structured output.** The answer is the *result* in
natural Portuguese — never the internal semantic request (`AGGREGATE ·
NET_REVENUE` is debug metadata only). It is produced by
`structured/rendering.py` directly from the validated rows: labels and values
exactly as returned, ties kept with their shared rank, order preserved,
notation `1.737` / `R$ 1.234,56` / `12,34%`, followed by a `Fonte: camada Gold — <tables>.` line
naming the tables the query actually read (names only, no catalog/schema; never
an `[Sx]` citation) (and, for a dimension with a rule such as salespeople, a line stating it).
One renderer per operation — an aggregate is one sentence ("O faturamento
líquido foi de R$ 1.234.567,89.", "Foram 12.345 pedidos efetivos.", "A taxa de
devolução foi de 4,32%."), a ranking has a natural title and numbered lines
("Os 5 produtos com maior receita líquida foram:"), a breakdown and a trend
have an introduction built from the metric, the dimension and the period
("Em 2025, o faturamento líquido por categoria foi:"; for a trend, the periods
actually returned: "Ao longo de 2025, o faturamento líquido mensal foi:", "De
janeiro a agosto de 2026, ...", "Em janeiro de 2026, ...") and one line per
group/period (trends chronological). There are **no per-question templates**: the wording is
presentation metadata in the registry (`MetricDef.display_pt`, `sentence_pt`,
`ranking_pt` and the value format `Unit` — `CURRENCY_BRL`, `INTEGER`, `UNITS`,
`DECIMAL`, `PERCENT`, `DAYS`) plus the dimension labels. **No model reproduces, reorders, summarizes or truncates
these values**, so the output token limit cannot cut them, and a
structured-only question makes no model call at all. A result larger than the
row cap (500) is refused, never truncated. Structured values never receive an
`[Sn]` citation and never appear in `sources`.

**`BOTH`.** The final answer is the deterministic structured block followed
by a documentary explanation written by the model from AI Search evidence
only (a prompt that never contains the structured values and forbids
restating them). The natural-language query reaches AI Search unchanged.
Documentary `[Sn]` citations and `sources` belong to the explanation only.

| Case | Internal status | HTTP | Public status | Answer | `sources` |
|---|---|---:|---|---|---|
| Structured OK (structured-only) | `STRUCTURED_DATA_OK` | 200 | `AI_SEARCH_OK` | deterministic answer | `[]` |
| `BOTH`: structured OK + documentary OK | `BOTH_OK` | 200 | `AI_SEARCH_OK` | values + explanation | documentary |
| `BOTH`: structured OK, no documentary evidence | `BOTH_DEGRADED` | 200 | `AI_SEARCH_OK` | values only | `[]` |
| `BOTH`: structured OK, documentary technical failure | `BOTH_DEGRADED` | 200 | `AI_SEARCH_OK` | values only | `[]` |
| Structured query returned zero rows | `STRUCTURED_DATA_NO_DATA` | 200 | `AI_SEARCH_NO_EVIDENCE` | none | `[]` |
| Structured query failed (incl. `BOTH`) | `STRUCTURED_DATA_FAILED` | 500 | `RAG_EXECUTION_FAILED` | none | `[]` |
| Unsupported analytical request | `STRUCTURED_DATA_UNSUPPORTED` | 200 | `AI_SEARCH_NO_EVIDENCE` | none | `[]` |
| Structured route selected, no service configured (hypothetical: this code deployed before 6B.2 configuration) | `STRUCTURED_DATA_UNAVAILABLE` | 500 | `RAG_EXECUTION_FAILED` | none | `[]` |

The asymmetry is deliberate: if the values cannot be produced, documentation
is **never** substituted for them; if only the documentary explanation is
missing, valid structured data is **never** discarded.

**Public API compatibility.** `/api/ask` request and response schemas and the
four public statuses are unchanged (see `API_CONTRACT.md`): the generic
structured layer is entirely behind the same `{"query": "..."}` contract.
Successful structured/`BOTH` responses currently map to the public
`AI_SEARCH_OK`, which means `AI_SEARCH_OK` can carry an `answer` with
`sources: []`. No frontend change is required. A dedicated public status and
structured provenance field are possible later, additively, and are not part
of this work.

**Adding another metric** is a registry entry: one `Metric` enum member, one
`MetricDef` (Power BI measure name, aggregate or numerator/denominator, unit,
allowed operations/dimensions/filters) and one vocabulary line in the parser
(plus a `FactDef` only if it needs a new Gold table). The compiler, validator,
renderer, service and executor do not change; a test asserts they mention no
individual metric. `AVERAGE_RECENCY` was added this way.

**What remains for Phase 6B.2 (live).**

- SQL Warehouse resource and `SQL_WAREHOUSE_ID`/`DATA_*` configuration for the
  App, and a live end-to-end validation (correctness of the generated SQL on
  Databricks SQL — e.g. `COUNT_IF`, `trunc`, `add_months`, `NULLS LAST` —
  cold-start latency, timeouts).
- Unity Catalog `SELECT` for the App service principal on **every Gold table
  the registry uses**: `fct_sales` and `dim_products` are granted;
  `dim_locations`, `dim_customers`, `dim_employees`, `fct_inventory_replenishment`,
  `fct_current_inventory` and `agg_customer_rfm` are not.
- Confirming the `'Salesperson'` role value in Gold, and that the date of
  `fct_sales.placed_at` (the sales business date; the deployed table has no
  `order_date`) agrees with Power BI's `dim_dates` relationship.
- A live smoke of the changed documentary prompt.

The currently deployed App does not contain this code; structured data stays
NOT LIVE until Phase 6B.2. If the code were deployed before that
configuration, a structured-routed question would return the controlled
`RAG_EXECUTION_FAILED` response (the structured service would be unavailable)
rather than a documentation answer.

## Interface Layer — Databricks App (Phase 8 — IMPLEMENTED, DEPLOYED to `dev`)

Databricks App is Layer 6 of the architecture, sitting above the AI
Harness. A backend now exists and is deployed:

```
Databricks App (rag/app.py, FastAPI)     [IMPLEMENTED, DEPLOYED dev]
  POST /api/ask  {"query": "..."}
      |
      v
  traced_ask()                           [IMPLEMENTED, Phase 7]
      |
      v
  AIHarness.ask()                        [IMPLEMENTED, Phase 6A]
      |
      v
  rag_pipeline.py (RagOrchestrator)      [IMPLEMENTED, Phase 5A]
   /       \
AI Search  Llama
```

**What exists:** a single-endpoint FastAPI backend (`rag/app.py`) that is
the App's entire V1 surface. Request contract is frozen to
`{"query": "..."}` — no `source_route`, `top_k`, model selection, or any
other retrieval control is accepted. Runtime (a `WorkspaceClient`, the
`RagOrchestrator`, the `AIHarness`) is constructed exactly once per App
process, not per request; `enable_autolog()` runs once at import time;
each request calls `traced_ask()` exactly once. For an `AI_SEARCH_OK`
result, the route calls `check_citation_integrity` and returns
`status`/`answer`/`sources`/`citation_integrity`/`message`; other statuses
map to a stable, minimal body with no internal exception text or stack
trace ever included (`result.error` is logged server-side only). HTTP
status is `200` for `AI_SEARCH_OK`/`AI_SEARCH_NO_EVIDENCE`, `400` for
`INVALID_REQUEST`, `500` for `RAG_EXECUTION_FAILED` or any status the V1
API does not otherwise expect. Since Phase 6B.1 the App also maps the
internal structured-data statuses onto these same four public statuses
(successful structured/`BOTH` answers → `AI_SEARCH_OK`, possibly with
`sources: []`; see "Structured Data & Source Routing" and
`API_CONTRACT.md`) — no public schema or status changed, and it is not live
until Phase 6B.2. The App does not call AI Search or the
Llama endpoint directly, does not classify the query itself, and does not
duplicate any Harness/Orchestrator/Policy logic.

**Deployment layout:** the Bundle App resource's `source_code_path` points
at `rag/` (not `rag/app`), so `rag/src/` deploys as a sibling of `app.py`
inside the App container — the same relative layout every other script in
this repository already assumes when it imports `from src...` with `rag/`
as the working directory. No RAG module is duplicated or copied into a
separate App directory.

**Dependencies:** `rag/requirements.txt` doubles as the App's own
dependency file (co-located with `app.yaml`, per the documented Databricks
App convention), and is deliberately separate from the shared dbt/root
environment (any `mlflow` install upgrades `databricks-sdk` past
`dbt-databricks==1.9.8`'s pin — see "Databricks Free Edition Constraints").
One concrete, live-discovered finding: `mlflow-tracing` (the lightweight
package) provides every API `tracing.py` uses when checked in an isolated
venv, but crashed the actual deployed App container on import
(`ImportError: cannot import name 'EvaluationDataset' from
'mlflow.entities'`) — the platform's own App base image ships mlflow-
related packages that conflict with `mlflow-tracing`'s slimmer
`mlflow.entities`. The full `mlflow` package resolves this as a strict
superset instead of a conflicting slim variant.

**Bundle integration:** the App is declared in `resources/rag_app.yml`
(auto-included via the existing root `databricks.yml`'s
`include: resources/*.yml` — no second Bundle project), alongside a
dedicated MLflow `experiments` resource
(`/Shared/${bundle.name}-${bundle.target}-rag-tracing`, not the earlier
throwaway spike path) that the App's `MLFLOW_EXPERIMENT_ID` env var
references. Runtime configuration (`RAG_CATALOG`, derived index name,
model endpoint) is environment-driven and reuses the existing Bundle
`catalog_name` variable — no personal/hardcoded catalog, schema, or user
path is embedded in App source or config. The App runs under its own
Databricks-generated service-principal identity (no static token, no
credentials file); `CAN_USE` is granted to an existing project principal
already present in `resources/resources.yml`.

**Deployed and LIVE VALIDATED (`dev` target only — `ci`/`prod` are not
deployed):**

- App resource `rag-api`, `app_status = RUNNING`, `compute_status = ACTIVE`.
- URL: `https://rag-api-7474653394852199.aws.databricksapps.com`.
- Unity Catalog access for the App's service principal is confirmed
  sufficient in practice: `USE_CATALOG` on
  `dev_natalia_correia_lh_nautical`, `USE_SCHEMA` on `...rag`, and
  `SELECT` on `document_chunks_index` are all held and were exercised by
  real, successful retrieval calls below — this is no longer an open
  blocker (see "Current Architectural Decisions" and "Roadmap" for the
  historical troubleshooting record, preserved as resolved history).
- All three V1 API-contract paths were exercised against the real
  deployed App, not merely called locally:

  | Query | HTTP | `status` | Notes |
  |---|---:|---|---|
  | `"GOLD-032"` | 200 | `AI_SEARCH_OK` | grounded answer, 5 sources, `citation_integrity.status = VALID` |
  | `"GOLD-999"` | 200 | `AI_SEARCH_NO_EVIDENCE` | `answer = null`, `sources = []`, no generation call made |
  | `"   "` (whitespace) | 400 | `INVALID_REQUEST` | no retrieval/generation call made, no internal error exposed |

  The `RAG_EXECUTION_FAILED` path was separately confirmed live during an
  earlier, now-resolved permission gap: the App returned `HTTP 500` with
  the correct stable minimal body and `result.error` confirmed absent
  from the response (visible only in `databricks apps logs rag-api`) —
  i.e. the controlled-error contract itself was validated live too, not
  only the success path.

**Deployment/runtime note (observed, not generalized beyond this case):**
`databricks bundle deploy` updates the Bundle-tracked App resource
definition and uploads source files, but in the one case observed here it
did **not** itself start a new App process — an App that was `CRASHED`
with `compute_status` still `ACTIVE` remained on its old, crashed process
after a `bundle deploy`. Reaching a fresh, healthy process required a
separate, explicit `databricks bundle run rag_app -t dev --restart`,
which created a new deployment/process that started cleanly. This is
recorded here as an observed runbook/troubleshooting fact for this
project's specific App resource, not as a general claim about Databricks
Apps' deploy semantics.

**Handoff contract:** `rag/API_CONTRACT.md` documents the exact `POST
/api/ask` contract (request/response JSON, status/HTTP-code mapping,
`sources[]`/`citation_integrity` field shapes, authentication, the
`CAN_USE` permission needed) for whoever builds the eventual UI on top of
this API — intentionally scoped to the contract only, not RAG internals.
The document is itself live-validated: its three example responses
(`AI_SEARCH_OK`/`AI_SEARCH_NO_EVIDENCE`/`INVALID_REQUEST`) are the actual
real response bodies from the three requests above, not hypothetical
examples. It also documents an **open integration decision**, not a
backend defect: CORS is not currently configured on the App, so a
browser-based frontend running outside Databricks (e.g. local dev) cannot
yet call `/api/ask` directly cross-origin — same-origin use from inside
Databricks is unaffected, and this does not block UI development against
the documented mock payloads. See "Databricks Free Edition Constraints"
for the App's compute posture.

**Backend/API vs. chatbot UI — do not conflate:** everything above is
backend/API-contract validation, and it is now **PASS/CLOSED** for that
scope. The chatbot UI itself (Layer 6's user-facing surface) is separate,
ongoing work by another contributor, built against `rag/API_CONTRACT.md`
— it is explicitly **not** part of what this section marks as validated.

## Top-K vs Token Budget (PLANNED)

Top-K retrieval and LLM context size are **different concerns** and must
not be conflated:

```
AI Search
    |
    v
Top-K candidate chunks
    |
    v
Context Manager
    |
    v
token-budget selection
    |
    v
selected evidence
    |
    v
generation model
```

The application should **not** automatically send every Top-K result to
the generation model — Top-K controls how many candidates are considered;
the Context Manager separately decides how many of those candidates
actually fit (and are worth including) within the model's token budget.
This design is still planned and not yet implemented. Retrieval-side Top-K
itself is now decided per route by the Phase 4C retrieval policy (5 for
`EXACT_RULE_ID`, 8 for `NORMAL`, 10 for `CROSS_SOURCE` — see "Retrieval
Architecture"); what remains an open decision here is the separate,
downstream **token budget** for the Context Manager (see "Open
Decisions"). In practice today (Phase 5A), `rag_pipeline.py` sends
**every** retrieved candidate straight into the grounding context with no
budget-based selection step — this is why the generation client's
`max_output_tokens` limit can be the thing that truncates a longer
grounded answer (see "Known Generation Limitations" in "Orchestration
Layer — LlamaIndex"), not a token-budget decision on the input side, which
does not exist yet.

## Response Verification / Groundedness (PARTIAL)

**Two of the four bullets below are implemented today.** (1) A narrow
deterministic guard inside `rag_pipeline.py` (Phase 5A): when retrieval
returns zero candidates (e.g. `"GOLD-999"` under `EXACT_RULE_ID`), the
orchestrator returns `evidence_available = false` / `answer = null` /
`sources = []` and makes **no model call** — see "Runtime validation" in
"Orchestration Layer — LlamaIndex". (2) Citation checking, via
`evaluation/citations.py`'s `check_citation_integrity(answer, sources)`
(Phase 6A/8): every `[Sn]` label the model's answer text actually used is
compared against the deterministic `sources` metadata Phase 5A already
returns, producing `VALID` (every cited label exists), `INVALID` (at
least one cited label does not exist in `sources`), or `NO_CITATIONS`
(the answer used no `[Sn]` marker at all). **This is a structural check
only** — `VALID` means every citation label the model used actually
exists; it does **not** mean the answer is correct, or that the cited
source actually supports the specific claim next to it (semantic
groundedness). It is called from the Databricks App (`app.py`), not from
`AIHarness.ask` itself. The remaining two bullets — Rule-ID presence
checking within the cited evidence, and a general evidence-sufficiency
judgment beyond the empty-retrieval guard — are **not implemented**. At
minimum, the system should eventually verify:

- evidence was actually retrieved (not an empty candidate set) — **DONE**;
- cited chunks genuinely exist in the retrieved context (no fabricated
  citation) — **DONE**, structural only (see above);
- cited Rule IDs are present in the supplied evidence — not implemented;
- the response has sufficient supporting evidence to be returned as-is
  (semantic groundedness) — not implemented.

Initial planned behavior:

```
retrieval
    |
    v
generation
    |
    v
deterministic verification
    |
    v
PASS -> answer
FAIL -> controlled insufficient-evidence response
```

Future evolution **may** include query reformulation, additional
retrieval, reranking, or a second verification pass — none of these loops
are implemented, and none should be assumed to exist.

## Quality, Evaluation and Observability (MLflow) (PARTIAL — tracing IMPLEMENTED, evaluation harness PLANNED)

MLflow is the transversal Quality/Observability plane (see "Final
Architecture — Six Layers"). Two distinct things must not be conflated:

- **A. Runtime AI Harness** — protects/controls each individual request in
  production (validation, source routing, citation checks — see "AI
  Harness"). Runs on every request. **Implemented** for the `AI_SEARCH`
  route (Phase 6A); context/token budget and general guardrails beyond the
  empty-retrieval guard remain not implemented.
- **B. MLflow Evaluation Harness** — systematically evaluates application
  *versions* during development and regression testing (not individual
  live requests). Runs on-demand / in CI-like cycles, not per-request.
  **Not implemented.**

**MLflow request tracing (Phase 7, feeding both A and B) is implemented**:
`harness/tracing.py`'s `enable_autolog()` turns on
`mlflow.llama_index.autolog()` once per process — this alone captures
`PolicyRetriever.retrieve`/`_retrieve` and `DatabricksLlamaLLM.complete` as
spans (chunks, scores, prompt, completion, token usage, model, latency),
with no manual instrumentation. `traced_ask()` adds one parent span around
`AIHarness.ask` so those two autologged spans nest under one request trace
instead of two disconnected ones, plus request-level attributes
(`SourceRoute`, `HarnessStatus`, resolved `RetrievalRoute`/`top_k`,
evidence count). Deployed and running against a real, portable, per-target
MLflow experiment (`/Shared/${bundle.name}-${bundle.target}-rag-tracing`,
declared as a Bundle `experiments` resource — see "Interface Layer —
Databricks App"), not the earlier throwaway spike path. The conceptual
trace shape actually realized (via autolog + the one manual parent span,
not a hand-built structure matching this exactly) is:

```
trace_id  (AIHarness.ask — manual parent span, harness/tracing.py)
├── SourceRoute / HarnessStatus / RetrievalRoute / top_k / evidence_count
├── PolicyRetriever.retrieve  (autologged — query, candidates, scores)
└── DatabricksLlamaLLM.complete  (autologged — prompt, completion, usage,
    model, latency)
```

Goals achieved today: debugging, retrieval inspection, latency and
usage/cost visibility (via autolog), one connected trace per request. Not
yet achieved: groundedness analysis, reproducibility/auditability tooling
beyond raw traces, and — see below — systematic evaluation.

## Evaluation (MLflow Evaluation Harness — "B" above)

MVP evaluation and later benchmarking are deliberately kept separate.

**MVP evaluation should focus on:**

- correct evidence retrieval;
- answer grounded in retrieved evidence;
- valid citations;
- controlled behavior when evidence is missing.

**Future/advanced metrics** (not required before the MVP is usable):

- Recall@K
- Precision@K
- MRR
- NDCG
- groundedness
- answer relevance
- embedding model comparison
- generation model comparison
- latency
- token usage / cost

Nothing above is implemented yet — see "Retrieval Architecture" and "AI
Harness" for the components an evaluation harness would eventually exercise.

## Testing

Local pure-Python test suite, last validated (executed) run:

```
1208 passed, 0 failed, 0 skipped
```

(Historical progression: 229 → 232 when the sixth canonical document was
added to the allowlist — see "Provenance" — → 275 with the Phase 4A.1
evidence-level evaluator tests (`test_evaluate_retrieval.py`) → 288 with
the Phase 4B identifier query-routing tests (`test_query_routing.py`) →
338 with the Phase 4C retrieval policy tests (`test_policy.py`) → 379 with
the Phase 5A orchestration tests (added across 7 files:
`test_orchestration_evidence.py`, `test_orchestration_retriever_adapter.py`,
`test_orchestration_context.py`, `test_orchestration_structured_data.py`,
`test_orchestration_orchestrator.py`, `test_orchestration_llm_adapter.py`,
`test_orchestration_no_oracle_leakage.py`) → 373 after the Lean RAG
Core review (373 is lower than 379, and this is not a coverage
regression: the Lean Core review removed the speculative
`structured_data.py` module entirely (see "Structured-data decision" in
"Orchestration Layer — LlamaIndex"), so its dedicated tests were removed
with it, and the remaining 6 Phase 5A test files were consolidated into 2
files mirroring the new module layout (`test_rag_pipeline.py`,
`test_llama_client.py`), which also removed a handful of duplicate
structural checks. Behavioral coverage for current runtime behavior —
retrieval-policy preservation, evidence translation, deterministic source
labels, grounding context, the Databricks Llama adapter, empty retrieval,
and cross-source behavior — remains intact in the consolidated files) →
**433** with the Phase 6A/7/8 additions: `test_harness.py` (20),
`test_citations.py` (16), `test_app.py` (11), `test_tracing.py` (13) —
373 + 20 + 16 + 11 + 13 = 433, confirmed by an actual full-suite run, not
computed/assumed) → **699** with the Phase 6B.1 additions:
`test_structured_service.py`, `test_structured_rendering.py`,
`test_source_router.py`, `test_harness_structured.py`,
`test_rag_pipeline_structured.py` and additional `test_app.py` cases, plus
`tests/conftest.py`, which keeps MLflow from writing anything into the
repository working directory during the run → **1208** with generic
Structured Analytics: `test_semantic_registry.py` (registry checked against the
dbt Gold YAML and the Power BI document), `test_analytical_compat.py`
(fail-closed compatibility matrix), `test_analytical_parser.py`,
`test_analytical_compiler.py` (every valid registry combination compiled and
checked for safety, plus the byte-level Phase 6B.0 regression),
`test_analytical_service_rendering.py` and `test_analytical_regression.py`
(end to end through the Harness with a fake executor). 1208 was confirmed by
an actual full-suite run in the RAG-scoped environment (`rag/.venv`), with no
SQL executed.)

- `test_loader.py` + `test_parser.py` + `test_chunker.py`: 156 (includes
  the 12 heading-only-section scenarios and 6 indivisible-lexical-segment
  scenarios from the retrieval-quality micro-patch, plus persistent
  validation against all 6 real canonical documents).
- `test_schema_contract.py`: 22 (field counts, order, `ingested_at`
  presence/absence, uniqueness, physical nullability, `rule_ids` array
  metadata, allowed logical types, source/target field-set difference).
- `test_persistence_validation.py`: 27 (every pre-write validation rule,
  including the zero-chunk-batch and 6-real-document/163-chunk end-to-end
  case).
- `test_persistence_expressions.py`: 27 (identifier parsing, SQL literal
  escaping, matched-update condition/set, insert mapping, scoped-delete
  condition for the extracted pure-Python MERGE-expression module).
- `test_evaluate_retrieval.py`: 56 (evidence-level Hit/Coverage/MRR/
  Precision/nDCG math, the mandatory `BR-03` false-success regression
  test, dataset-loading validation, generic-heading detection — all pure,
  no Databricks dependency).
- `test_query_routing.py`: 20 (Rule ID extraction, deterministic filter
  construction, deterministic query expansion, and a mechanical guard that
  this module never imports the evaluation package — no oracle leakage).
- `test_policy.py`: 30 (exact-identifier detection incl. whitespace/case
  variants and nonexistent-but-valid IDs, conservative cross-source
  classification incl. its known false negative, routing precedence, and
  the same no-oracle-leakage guard applied to the policy layer).
- `test_rag_pipeline.py` (Phase 5A, consolidated by the Lean Core review):
  evidence translation and metadata preservation, deterministic `S1`/`S2`/
  ... source labeling, context/prompt block formatting, the LlamaIndex
  `PolicyRetriever` adapter (confirms it passes the `RetrievalPolicyDecision`'s
  `top_k`/`query_type`/`filters_json` through unchanged for
  `EXACT_RULE_ID`/`NORMAL`/`CROSS_SOURCE`), the orchestrator's empty-retrieval
  guard, and the same no-oracle-leakage guard applied to the pipeline
  module. All AI Search/Databricks calls are injected fakes — no network.
- `test_llama_client.py` (Phase 5A, renamed from `test_orchestration_llm_adapter.py`):
  `DatabricksLlamaLLM`'s message-role mapping, centralized default
  configuration, token-usage extraction (present/absent), and the explicit
  `NotImplementedError` on streaming — the Databricks serving-endpoint call
  is always a fake; the real network call is exercised only by the Phase 5A
  runtime smoke tests documented in "Orchestration Layer — LlamaIndex".

No PySpark, `delta-spark`, or Java is installed or used anywhere in this
suite — `spark_schema.py`, `persistence.py`, and `run_ingestion.py` compile
syntactically but are not imported or executed locally. Likewise,
`ai_search.py`, `run_baseline.py`, `run_identifier_experiments.py`,
`run_policy_smoke_test.py`, `run_policy_regression.py`, and the live
Databricks Llama endpoint call inside `generation/llama_client.py` are
Databricks-dependent, not unit tested directly — only the pure metric/
routing/translation logic around them is. This is a deliberate boundary,
not a gap: real Spark/Delta/`MERGE`/CDF/AI Search retrieval, policy
behavior, and end-to-end orchestration+generation **have been separately
validated against real Databricks runtime** — see "Delta Runtime
Validation Results", "Retrieval Architecture", and "Orchestration Layer —
LlamaIndex" above. This local suite passing is one input among several;
Databricks runtime validation is documented independently, not inferred
from it.

**Added for Phase 6A/7/8, folded into the totals above (433 at that point):**
`test_harness.py` (20 — `AIHarness.ask` request validation, source
routing, controlled-failure containment; `RagOrchestrator` always a fake,
no network), `test_tracing.py` (13 — `enable_autolog`/`traced_ask`
behavior, including root-span selection robust to autologged child spans
from a genuinely LlamaIndex-instrumented fake orchestrator/LLM), plus
`test_citations.py` (16 — `check_citation_integrity`'s
`VALID`/`INVALID`/`NO_CITATIONS` logic, pure) and `test_app.py` (11 —
FastAPI-`TestClient` cases against `app.py` with `traced_ask`
monkeypatched — no Databricks, no live MLflow). `test_harness.py` and
`test_citations.py` need no App-scoped dependency; `test_tracing.py` and
`test_app.py` require the App-scoped `mlflow`/`fastapi` dependencies (see
"Interface Layer — Databricks App") and are skipped, not failed, wherever
those aren't installed (`pytest.importorskip`) — the 1208 total above was
obtained in an environment where all of them were importable, so none
were skipped in that run.

## Provenance

`rag/` is now versioned in Git (branch `feature/rag`). When a canonical
document's ingested content is provably identical (after normalization) to
the version tracked at `HEAD`, `source_git_commit` is populated with that
commit SHA; otherwise (untracked file, uncommitted changes, or content
diverging from `HEAD`) it resolves to `NULL` — see the Loader section above
for the exact rule.

An idempotent rerun (runtime-validated — see "Delta Runtime Validation
Results": 0 inserts, 0 updates, 0 deletes, 99 unchanged) requires the same
canonical content **and** the same Git provenance state across both runs.
A change in Git state between Run 1 and Run 2 — for example a new commit
landing on `feature/rag` between the two runs — can legitimately change
`source_git_commit` for affected documents and therefore trigger metadata
updates on their chunks. That is expected provenance metadata drift (see
"Document and Chunk Change Control" — metadata update case), not a failure
of idempotency. The runtime-validated first-run / identical-rerun result
above was obtained against a stable Git state, which is required to
produce a clean 0/0/0 result on the second run.

**Observed for the sixth document (runtime-validated, not an error):** all
64 `powerbi_semantic_rules` chunks currently have `source_git_commit =
NULL`. This is the loader's documented, correct behavior — the working
tree's `powerbi_semantic_rules.md` content differs from what `HEAD` tracks
at that path (`HEAD` still tracks the original empty placeholder), so
`source_git_commit` correctly resolves to `NULL` rather than a stale or
incorrect commit SHA. This is not treated as an error, and no commit was
made solely to populate this field.

## Roadmap — Project Completion (Next Steps to a Usable Databricks App)

Status legend used below: **DONE** (implemented and closed for current
scope), **CURRENT** (implemented but with a specific, named remaining
step before it's closed), **NEXT** (the immediate next unblocked step),
**PLANNED** (not started; a later phase).

**Immediate next steps, in order:**

1. **[NEXT — Phase 7 remainder]** Build the MLflow evaluation harness
   (systematic retrieval/answer-quality regression) — tracing itself is
   now DONE and live-validated (see "Interface Layer — Databricks App"),
   but that alone does not cover systematic quality regression.
2. **[IN PROGRESS — Phase 8 remainder, separate contributor]** Full
   chatbot UI beyond the current API contract — out of scope for the
   backend work, which is DONE/live-validated;
   `rag/API_CONTRACT.md` is the handoff point another contributor is
   already building against.
3. **[OPEN — Phase 8 integration decision]** Resolve the external-browser
   CORS/authentication integration decision documented in
   `rag/API_CONTRACT.md` (CORS is not currently configured) — only needed
   if the chatbot UI ends up running as a browser app outside Databricks;
   same-origin use inside Databricks is unaffected.
4. **[PLANNED — Phase 9]** Multi-source document pipeline, once the core
   RAG + App are fully closed out above.

(Resolved since the previous revision of this section — kept out of the
list above: the Unity Catalog `SELECT`/`USE_CATALOG`/`USE_SCHEMA` grant
gap that previously blocked live App validation; live `GOLD-032`/
`GOLD-999`/invalid-query validation through the deployed HTTP endpoint;
MLflow trace-record inspection for those live requests.)

### Phase 1 — Knowledge Engineering — **DONE**

**STATUS: PASS — current canonical corpus complete for present scope.** 6
canonical documents / 163 current chunks.

- canonical Markdown (6 documents, including the PBIP/TMDL-curated Power
  BI semantic document)
- loader
- parser
- chunker
- deterministic hashes/IDs
- persistence validation (`persistence_validation.py`, `persistence_expressions.py`)
- Delta MERGE implementation (`persistence.py`)
- local test suite (232 passed, 0 failed, 0 skipped)
- Power BI semantic rules: promoted — see "Corpus Completion Checkpoint"
  below and "Power BI Semantic Rules" above.

### Phase 2 — Delta Persistence Runtime Validation — **DONE**

**STATUS: PASS / CLOSED.**

- `run_ingestion.py` entrypoint fixed — runs correctly as a Databricks
  `spark_python_task` (falls back to `Path.cwd()` plus structural
  root-finding when `__file__` is undefined; see "Persistence Layer").
- First ingestion run — runtime-confirmed: 99 inserted, 0 updated, 0
  deleted, 99 total rows.
- Identical rerun — runtime-confirmed: 0 inserted, 0 updated, 0 deleted,
  99 unchanged.
- `DESCRIBE HISTORY` validated for both `MERGE` operations
  (`operationMetrics` inspected directly).
- CDF validated (99 `insert` records on the first `MERGE`'s version; zero
  records on the identical rerun's version).

### Phase 3A — AI Search Runtime Infrastructure — **DONE**

**STATUS: PASS / CLOSED.**

- Qwen3 (`databricks-qwen3-embedding-0-6b`) accepted as a managed
  embedding — the only conclusive validation available was creating the
  real target index, which is permitted since it is this project's actual
  index.
- `document_chunks_index` created on the shared `rag_endpoint`.
- `pipeline_type = TRIGGERED`; initial creation auto-synced without a
  manual `sync()` call.
- 99/99 rows indexed, `ready = true`.
- `index_subtype = HYBRID`.
- Hybrid Search smoke tests: PASS (5 Portuguese queries across the 5
  knowledge categories, majority-relevant, fully traceable results).

### Corpus Completion Checkpoint — **DONE**

**STATUS: PASS / CLOSED.**

```
PBIP/TMDL inspected
        |
        v
powerbi_semantic_rules.md curated
        |
        v
human review gates passed
        |
        v
promoted to canonical allowlist
        |
        v
incremental ingestion validated
        |
        v
triggered sync validated
        |
        v
retrieval sanity checks passed
```

- Inspected PBIP/TMDL semantic-model evidence (`Dashboard Gerencial
  Compass 360 - LH Nautical` project) and curated `powerbi_semantic_rules.md`
  from it — see "Power BI Semantic Rules" above.
- Document validated (structure, DAX-to-TMDL traceability, cross-checked
  against the existing 5 documents) and promoted into `CANONICAL_DOCUMENTS`
  (`document_id = powerbi_semantic_rules`, `canonical_domain =
  powerbi_semantics`, `document_status = CLOSED`) — see "Canonical
  Knowledge Base" above.
- Incremental ingestion — runtime-confirmed, not expected:
  `PersistenceResult(inserted=64, updated=0, deleted=0, unchanged=99)`.
  The 99 pre-existing chunks were verified byte-identical before/after
  (same `chunk_id` set, same `content_hash`, same `document_hash`, same
  `ingested_at`) — see "Delta Runtime Validation Results" above.
- One `TRIGGERED` AI Search index sync performed — runtime-confirmed:
  `ready=true`, 163/163 rows indexed, configuration unchanged — see
  "Retrieval Architecture" above.
- Retrieval sanity checks passed: the original 5-domain smoke tests
  regression-tested clean against the expanded corpus, plus 5 new Power
  BI-specific smoke tests, plus one cross-source smoke test (see
  "Retrieval Architecture" above).

### Phase 3B — AI Search Infrastructure as Code — **PLANNED** (non-blocking)

**STATUS: PLANNED / NON-BLOCKING HARDENING** for `document_chunks_index` itself. Note: the Databricks App and its MLflow experiment *are* now Bundle-managed (Phase 8) — this phase is specifically about the vector-search index resource, not "no IaC anywhere in rag/".

- represent/manage `document_chunks_index` through the Asset Bundle, if
  supported appropriately for a Vector Search index resource;
- avoid destructive recreation of the existing, live index merely to
  introduce IaC;
- retain the existing shared `rag_endpoint` and the existing validated
  index configuration (no new endpoint, no reconfiguration).

Phase 3B remains important for reproducibility and infrastructure
management, but was correctly **not** a prerequisite for completing the
Power BI semantic corpus (the checkpoint above) — the promotion above
completed independently of it, confirming that non-blocking sequencing.

### Phase 4 — Retrieval Quality & Policy — **DONE**

**STATUS: PASS / CLOSED FOR CURRENT RETRIEVAL SCOPE.** This does **not**
mean retrieval is perfect or that every open retrieval question is
resolved — see "Retrieval Quality — Known Limitations" (in "Retrieval
Architecture" above) for what remains open. It means: a formal,
evidence-level evaluation was run; controlled, isolated experiments were
run on the resulting hypotheses; and a deterministic, evidence-derived
routing policy was implemented and runtime-validated on top of the
existing AI Search index — with no knowledge_base, ingestion, Delta, or AI
Search index configuration changes anywhere in this phase.

**Sub-phases (all closed):**

- **4A — Retrieval Quality Baseline.** First formal evaluation: 37-query
  versioned dataset, Top-K experiments, latency, generic-heading analysis,
  negative/OOD diagnostics. **Superseded by 4A.1** — its evaluator had a
  document-level-match bug (see below); its raw results are preserved as
  historical artifacts (`rag/evaluation/results/baseline_v1/`), not the
  official baseline.
- **4A.1 — Evidence-Level Metric Correction.** The evaluator was corrected
  so that success requires matching `expected_evidence` (document +
  exact Rule ID or normalized section cue), never a bare document match.
  Official corrected baseline (K=3/5/8/10/15): Hit@K 0.939→0.970,
  Coverage@K 0.894→0.970, Precision@K 0.455→0.125 (precision *falls* as K
  grows — expected), nDCG@K 0.822→0.799, MRR = 0.758. Key findings:
  `BR-03`/`"GOLD-032"` entirely absent from Top-15 under plain HYBRID;
  `FC-05`'s evidence at rank 6; 26 of 31 generic-heading Top-5 occurrences
  graded irrelevant once evidence-level grading applied; 3 of 4
  negative/OOD queries scored close to genuine positives (retrieval score
  alone is not a reliable evidence-sufficiency signal — no threshold
  defined). See "Retrieval Quality Baseline (Phase 4A / Phase 4A.1)"
  above for full detail.
- **4B — Controlled Retrieval Experiments.** A dedicated 14-identifier
  dataset, evaluated under 4 isolated configurations: plain HYBRID (A0,
  MRR=0.045) confirmed systemically weak for exact `GOLD-ID` queries — not
  a `BR-03`-only quirk; `FULL_TEXT` (A1, MRR=0.443) and a `rule_ids`
  metadata filter (A2, MRR=0.631, the strongest) both validated as
  identifier routes; deterministic query expansion (A3, MRR=0.068) showed
  no meaningful improvement. The Databricks reranker was probed and found
  `UNAVAILABLE_IN_CURRENT_ENVIRONMENT` (workspace-level configuration
  block) — not evaluated as ineffective, simply not accessible here. All
  findings are specific to the identifier evaluation subset, not a general
  claim about non-identifier retrieval.
- **4C — Retrieval Policy.** Implemented `rag/src/retrieval/policy.py`: a
  small, deterministic, testable (not LLM-based) classifier routing
  `EXACT_RULE_ID` → HYBRID + `rule_ids` filter / Top-K=5,
  `CROSS_SOURCE` → HYBRID / Top-K=10, `NORMAL` (default) → HYBRID /
  Top-K=8. Top-K values derived from an offline re-analysis of existing
  Phase 4A.1 results (zero new AI Search calls). Runtime-validated with 5
  live smoke queries (including the nonexistent-ID case, which correctly
  returns zero results with no semantic fallback, and the Ticket
  Médio Gold+Power BI cross-source case, which recovers both expected
  evidence units within Top-10). Offline policy-level regression across
  33 positive evaluation queries: **zero evidence misses observed**
  (dataset-specific finding, not a general accuracy claim) — see
  "Retrieval Policy (Phase 4C)" above for full detail, including the
  known false negative in cross-source classification and the small
  (3-query) cross-source Top-K sample size.

### Phase 5A — LlamaIndex + Databricks Llama Orchestration — **DONE**

**STATUS: `ORCHESTRATION_RUNTIME_VALIDATED` / CLOSED.** This means the
technical RAG path works end to end under the intended architecture — it
does **not** mean answer quality is formally evaluated, hallucination is
solved, evidence sufficiency is solved, citation integrity is solved, the
Harness is implemented, MLflow is implemented, or structured ML data is
integrated. See "Orchestration Layer — LlamaIndex" and "Generation
Design" above for full detail.

- Custom LlamaIndex `BaseRetriever` (`PolicyRetriever`) wrapping the
  existing `ai_search.py` client — the official `DatabricksVectorSearch`
  integration was evaluated and not adopted for the runtime path, to avoid
  a second query path that could diverge from the already-validated
  HYBRID/`rule_ids`-filter/Top-K/metadata semantics.
- Foundation Model API integration — `databricks-meta-llama-3-3-70b-instruct`,
  confirmed `READY`, via `generation/llama_client.py`'s `DatabricksLlamaLLM`
  (a LlamaIndex `CustomLLM`), authenticated through the existing Databricks
  SDK profile mechanism — no static token, no secret in the repository.
- Llama generation wiring — `orchestration/rag_pipeline.py`'s
  `RagOrchestrator.generate`: retrieval policy execution → evidence
  translation → deterministic context assembly → generation → deterministic
  source metadata.
- 5 live runtime smoke calls (exact-identifier, nonexistent-identifier,
  normal, cross-source, forecasting) all matched the offline-predicted
  retrieval route and produced grounded generation (or, for the
  nonexistent-identifier case, correctly no generation at all) — see
  "Runtime validation" above.
- 1 OOD diagnostic call (informational only, not a success gate).
- Future structured-data query tool/interface path (see "Structured
  Runtime Data — Analytical / ML Outputs (PLANNED)") remains conceptual
  only — no schema, no provider, no ML predictions implied to exist.

### Architecture Scope Review — Lean RAG Core — **DONE**

**STATUS: `LEAN_CORE_REFACTORED` / CLOSED.** Immediately after Phase 5A,
the orchestration package was inspected for real usage before any further
phase built on top of it. Finding: zero external consumers of the six
initially separate orchestration files existed anywhere in the codebase
— the split was a strictly linear chain (evidence → retriever adapter →
context → orchestrator) with no independent reuse to justify multiple
modules for the current project size. Consolidated into one module,
`orchestration/rag_pipeline.py`, plus one relocated file,
`generation/llama_client.py`; removed the speculative, zero-caller
`structured_data.py` entirely (its future boundary is now documented in
prose, not code — see "Structured-data decision" above). No runtime
behavior changed: verified via the full test suite and one live
re-run of the exact-identifier and nonexistent-identifier cases after the
refactor — see "Lean refactor behavioral validation" above.

### Phase 6A — AI Harness Core — **DONE** (for `AI_SEARCH` route scope)

**STATUS: PASS / CLOSED FOR `AI_SEARCH`-ROUTE SCOPE.** Implemented without
requiring ML/structured data, exactly as scoped — uses the existing,
unmodified Retrieval Policy and `rag_pipeline.py`. See "AI Harness" above
for full detail; summary:

- request validation — **DONE** (`AIHarness.ask`, `INVALID_REQUEST` on
  empty/whitespace query, never reaches `RagOrchestrator`).
- `AI_SEARCH` source route — **DONE** (default route; structured-data
  routing was added locally in Phase 6B.1 and is not live until 6B.2).
- use of the existing Retrieval Policy (Phase 4C) and orchestrator (Phase
  5A) as-is — **DONE**, no re-implementation.
- controlled-failure containment — **DONE**: any exception from
  `RagOrchestrator.generate` is caught and returned as
  `RAG_EXECUTION_FAILED`, never propagated uncaught.
- citation validation — **DONE**, but as a separately reusable function
  (`evaluation/citations.py`), not inside `harness.py` — called by the App
  layer (see "AI Harness" and "Response Verification / Groundedness").
- tracing hooks — **DONE**, but as an external wrapper
  (`harness/tracing.py`'s `traced_ask`), not inside `harness.py` (see
  "AI Harness").
- context/evidence controls (candidate selection, token budget) — **NOT
  DONE**, remains open (see "Top-K vs Token Budget"); `RagOrchestrator`
  still sends the full retrieved evidence set.
- general guardrails/evidence-sufficiency judgment beyond the Phase 5A
  empty-retrieval guard — **NOT DONE**.

### Phase 6B — Structured Data Integration — **6B.1 IMPLEMENTED LOCALLY; 6B.2 (LIVE) PLANNED**

**STATUS: PARTIAL.**

- **Phase 6B.0 — runtime discovery — DONE.** Read-only validation of the
  Gold-layer product-ranking query (join, effective-sales population, dense
  ranking) outside the App.
- **Phase 6B.1 — local implementation — DONE (unit-tested, NOT LIVE).**
  Deterministic rule-based source router; `StructuredQueryService` with an
  injectable SQL executor — first one allowlisted intent
  (`TOP_PRODUCTS_BY_QUANTITY`), then generalized to **Structured Analytics**:
  a registry of 15 approved metrics and 9 dimensions taken from the Power BI
  semantic model, a typed `AnalyticalRequest`, a compatibility matrix and a
  deterministic SQL compiler (still no model-generated SQL; the old intent is
  compiled by the same code and its statement is unchanged); deterministic rendering of ranked values (no model reproduces
  them); `BOTH` = deterministic block + documentary explanation, degrading
  to the structured block if the explanation is unavailable; fail-closed
  behavior for unsupported analytical requests and for structured failures;
  public API contract unchanged. See "Structured Data & Source Routing".
- **Phase 6B.2 — live validation — PLANNED / NEXT.** SQL Warehouse resource
  and configuration for the App, service-principal grants on the Gold
  tables, live end-to-end validation (correctness, cold-start latency,
  timeouts) and a live smoke of the changed documentary prompt.
- ML / forecast outputs — **still DEFERRED UNTIL A REAL DATA CONTRACT
  EXISTS**; no physical schema is invented here.

### Phase 7 — MLflow Quality Plane — **CURRENT** (tracing done; evaluation harness remains)

**STATUS: PARTIAL.**

- tracing — **DONE** (`harness/tracing.py`; see "Quality, Evaluation and
  Observability (MLflow)" above for full detail).
- evaluation harness — **PLANNED / NEXT** (systematic retrieval/answer
  quality regression against a stable dataset; not individual live
  requests).
- retrieval evaluation — **PARTIAL**: Phase 4A.1's evidence-level
  evaluator (`evaluate_retrieval.py`) already exists and was used for the
  retrieval-quality baseline, but it is not wired into an MLflow
  evaluation harness or run on a regression cadence yet.
- answer evaluation — **PLANNED**.
- regression tests (RAG-quality, not unit tests) — **PLANNED**.
- latency/usage metrics — **PARTIAL**: captured per-request today via
  MLflow autolog (latency, token usage, model), but not yet aggregated or
  tracked over time.

### Phase 8 — Databricks App — **DONE** (backend/API; chatbot UI remains separate ongoing work)

**STATUS: backend/API functional validation PASS / CLOSED.** See
"Interface Layer — Databricks App" above for full detail; summary:

- FastAPI backend (`rag/app.py`), frozen `POST /api/ask` contract —
  **DONE**.
- connect to AI Harness (via `traced_ask`) — **DONE**.
- Bundle integration (`resources/rag_app.yml`, reusing the existing root
  Bundle) — **DONE**.
- `dev` deployment (App `RUNNING`/`ACTIVE`, reachable over real HTTP) —
  **DONE**.
- deployment validation (controlled-error contract shape, confirmed live
  for a failing request) — **DONE**.
- full live-request validation through the deployed HTTP endpoint —
  **DONE**: `"GOLD-032"` → `AI_SEARCH_OK` (5 sources, `citation_integrity
  = VALID`), `"GOLD-999"` → `AI_SEARCH_NO_EVIDENCE`, whitespace query →
  `INVALID_REQUEST`/HTTP 400 — all three exercised live, not just
  code-reviewed. The Unity Catalog permission gap that previously blocked
  this is resolved (`USE_CATALOG`/`USE_SCHEMA`/`SELECT` on
  `document_chunks_index` all confirmed held and exercised by the App's
  service principal) — preserved as resolved history in "Current
  Architectural Decisions", not as a current blocker.
- real MLflow trace inspection for live App requests — **DONE at the
  trace-record level**: parent `AIHarness.ask` span, correct
  inputs/outputs, `mlflow.user` matching the App's own service principal,
  real token usage/cost, and span counts consistent with each request's
  code path were all confirmed for live `GOLD-032`/`GOLD-999`/invalid-query
  traces. Individual child-span artifacts (the retrieval/generation
  spans' own attributes) could not be retrieved with the read-only
  tooling used — this remains a **non-blocking observability
  limitation**, not something claimed as inspected.
- chatbot UI — **PLANNED / IN PROGRESS (separate work)**, explicitly out
  of scope for this phase's backend work; `rag/API_CONTRACT.md` is the
  live-validated handoff contract another contributor is building the UI
  against. Do not read Phase 8's backend closure as chatbot UI closure.

### Phase 9 — Multi-Source Document Pipeline — **PLANNED**

**STATUS: PLANNED.**

- Unity Catalog Volume
- PDF/DOCX/PPTX/HTML
- source hashing
- normalization
- quality gates
- document registry
- document promotion
- documental index

Functional phases advance only from validated upstream behavior — the
Corpus Completion Checkpoint followed Phase 3A and is now closed; Phase 4
(with its 4A/4A.1/4B/4C sub-phases) followed it and is now closed for the
current retrieval scope; Phase 5A followed it and is now closed
(`ORCHESTRATION_RUNTIME_VALIDATED`); the Architecture Scope Review — Lean
RAG Core immediately followed Phase 5A and is now closed
(`LEAN_CORE_REFACTORED`); Phase 6A — AI Harness Core followed it and is
now closed for its scoped `AI_SEARCH`-route responsibilities; Phase 7
(tracing) and Phase 8 (App backend + dev deploy) are both now partially
done, each with one specific named remaining step (an MLflow evaluation
harness; a Unity Catalog grant), not a full re-scope. Phase 6B.1
(structured routing, generalized to Structured Analytics) was later implemented
locally and is not live until Phase 6B.2; ML/forecast outputs remain
deferred until a real data contract exists — nothing in Phase 7/8 required
it. Infrastructure-hardening work such as Phase 3B may
proceed independently (before, during, or after a functional checkpoint)
when it does not change runtime semantics or block the next functional
step — confirmed in practice, since Phase 3B remained PLANNED throughout
both the Corpus Completion Checkpoint and Phase 4 without blocking either,
and the Databricks App/MLflow-experiment Bundle resources (part of Phase
8) were added without it. The Corpus Completion Checkpoint and the Lean
RAG Core review are not themselves numbered as phases.

## Compute Requirements by Phase

Concepts, not numbers — no DBU or cost figures are asserted here (see
"Databricks Free Edition Constraints"). The project should avoid
unnecessary continuously-running workloads; prefer triggered/on-demand
execution where appropriate for this MVP.

- **Phase 1/2 — Knowledge Engineering / Delta runtime validation.**
  Compute: serverless job/task execution for `run_ingestion.py`; SQL
  warehouse queries for validation (`DESCRIBE HISTORY`, CDF, row counts).
  Usage: temporary, only during an ingestion run or a validation check —
  no standing compute.
- **Phase 3A/3B — AI Search.** Compute/serving: one initial Delta Sync per
  index creation; managed embedding generation during sync; AI Search
  serving while the index exists (`rag_endpoint`, shared with other
  projects); `TRIGGERED` sync only when explicitly requested, never
  `CONTINUOUS`.
- **Phase 4 — Retrieval evaluation.** Compute: AI Search queries issued by
  evaluation/experiment scripts (`run_baseline.py`,
  `run_identifier_experiments.py`, `run_policy_regression.py`).
- **Phase 5A — LlamaIndex + Llama generation.** Compute/usage: AI Search
  requests plus Foundation Model API token usage (pay-per-token, not
  provisioned throughput).
- **Phase 6A — AI Harness.** Compute: the calling process's own runtime
  (no separate compute layer) plus whatever the retrieval/generation calls
  underneath it use.
- **Phase 7 — MLflow.** Compute: the LLM/retrieval calls tracing wraps
  (already counted above) plus MLflow trace storage/logging itself.
  Storing MLflow metadata does **not**, by itself, require any permanent
  or continuously-running compute.
- **Phase 8 — Databricks App.** Compute: the App's own runtime container
  (`compute_size: MEDIUM`, observed via `databricks apps get` — a real,
  currently-running App, not a projection) plus downstream AI Search and
  LLM inference calls it triggers per request.
- **Phase 9 — Document processing (future).** Compute: parsing/extraction,
  chunking, Delta writes, embedding/index sync — same triggered pattern as
  Phase 1–3, not continuous.

## Definition of Done

**MVP Definition of Done** — the project is complete when all of the
following hold:

1. deterministic canonical ingestion validated in Databricks — **DONE**
   (Phase 1/2).
2. idempotent Delta MERGE validated — **DONE** (Phase 2).
3. AI Search index `ONLINE`/`ready=true` and reproducible — **DONE**
   (Phase 3A; not yet Bundle-managed as IaC — Phase 3B, non-blocking).
4. retrieval quality baseline — **DONE** (Phase 4).
5. LlamaIndex orchestration working — **DONE** (Phase 5A).
6. Databricks-hosted Llama generating grounded answers — **DONE**
   (Phase 5A).
7. AI Harness enforcing evidence/context/citation rules — **PARTIAL**:
   request validation, source routing, and citation validation are
   implemented (Phase 6A); context/token budget and general
   evidence-sufficiency guardrails beyond the empty-retrieval guard are
   not.
8. MLflow tracing and evaluation baseline — **PARTIAL**: tracing is
   implemented, deployed, and **live-validated** (real trace records for
   real live App requests — see Phase 7/8; individual child-span
   artifacts remain a non-blocking observability limitation, not a
   functional gap); the evaluation baseline/harness is still **PLANNED**.
9. Databricks App usable as a chatbot — **PARTIAL**: the backend API is
   implemented, deployed to `dev`, and **live-validated** end to end
   (`GOLD-032`/`GOLD-999`/invalid-query all exercised through the real
   deployed HTTP endpoint — Phase 8 backend/API scope is DONE); there is
   still no chatbot UI (separate, ongoing work by another contributor,
   built against `rag/API_CONTRACT.md`).
10. reproducible documentation — **DONE** (this document).
11. regression evaluation — **PLANNED** (part of item 8/Phase 7).
12. controlled failure when evidence is insufficient — **DONE** for the
    empty-retrieval case (Phase 5A) and for App-level request/execution
    failures (Phase 8); **not done** for a general semantic
    evidence-sufficiency judgment.

**Post-MVP / Multi-Source Expansion Definition of Done** (does not block
the MVP above):

- Unity Catalog Volume-based raw document intake.
- PDF/DOCX/PPTX/HTML parsing, normalization, and quality gates.
- Document registry with auto-approval/review/rejection states.
- A documentary/source index alongside the canonical `document_chunks_index`,
  with the AI Harness able to route between canonical-only, documentary-only,
  or both.

## Current Architectural Decisions

1. Git is the source of truth for the current canonical Knowledge Base.
2. Five of the six current Markdown documents were derived from data
   exploration (Gold/dbt/SQL); the sixth (`powerbi_semantic_rules.md`) was
   derived from direct PBIP/TMDL semantic-model inspection. Neither path
   involves PDFs.
3. Existing PDFs are a separate documentary/reference source, not part of
   the current corpus.
4. Markdown is the internal canonical textual representation used for
   retrieval — not a required client input format.
5. Raw external documents should not necessarily be versioned in Git.
6. New external documents will eventually pass through parsing,
   normalization, and quality gates before becoming canonical Markdown.
7. Auto-approval will require both a trusted source and passing technical
   validation — neither alone is sufficient.
8. Current incremental change control operates at the chunk level
   (`content_hash` + deterministic `chunk_id`).
9. Whole-document retirement is controlled by `document_id`, not by any
   individual `chunk_id`.
10. Unexpected source disappearance must fail safe rather than trigger
    automatic deletion of a document's chunks.
11. `document_chunks` is the Databricks AI Search source table, indexed via
    a `TRIGGERED` Delta Sync pipeline — `document_chunks_index`, created
    and runtime-validated (Phase 3A), on the shared `rag_endpoint`, kept in
    sync with the current 163-row corpus (Corpus Completion Checkpoint).
12. Hybrid Search (vector + lexical) is the selected retrieval strategy,
    not vector-only — implemented and formally evaluated with an
    evidence-level evaluator (Phase 4A.1), not just smoke-tested.
13. Top-K retrieval is a separate concern from context/token budgeting.
    Retrieval-side Top-K is now decided per route by the Phase 4C policy
    (5 for `EXACT_RULE_ID`, 8 for `NORMAL`, 10 for `CROSS_SOURCE`); the
    downstream Context Manager's token budget remains a separate, still
    open decision (see "Top-K vs Token Budget").
14. Managed embeddings (via AI Search) are preferred over persisting
    embeddings manually in `document_chunks`.
15. Qwen3-Embedding-0.6B is validated as the managed embedding model for
    this index (Phase 3A) — multilingual, already validated as accessible
    in this workspace, and now confirmed accepted as
    `embedding_model_endpoint_name` by a real, `ready=true` Delta Sync
    Index. No other embedding model has been formally benchmarked against
    it.
16. Generation is **Databricks-hosted Llama via the Foundation Model API**
    (final decision, implemented and runtime-validated — Phase 5A). Claude
    is not part of the active architecture: `generation/claude_client.py`
    and `generation/search_results.py` remain 0-byte legacy placeholders,
    unused by any current code path.
17. Generation keeps a minimally model-agnostic interface — the rest of
    the application depends on the plain LlamaIndex `LLM`/`CustomLLM`
    interface, not on `DatabricksLlamaLLM` by name outside `generation/`
    — to allow swapping between Databricks-hosted models later; this does
    not reopen Claude or any other vendor as a roadmap item.
18. LlamaIndex is selected for, and now implements (Phase 5A), runtime
    orchestration (Layer 4, `orchestration/rag_pipeline.py`) — it does not
    own ingestion, persistence, or quality control, and it does not
    re-decide the Retrieval Policy (Phase 4C remains authoritative for
    query classification, Top-K, and filtering).
19. The AI Harness is this project's own custom application-control layer
    (Layer 5) — not delegated to LlamaIndex, `rag_pipeline.py`, or any
    third-party framework. It sits above the orchestration layer, not
    inside it. Implemented and live-validated for the `AI_SEARCH` route
    (Phase 6A); `STRUCTURED_DATA`/`BOTH` implemented and unit-tested locally
    (Phase 6B.1), live validation deferred to Phase 6B.2.
20. MLflow is the transversal Quality/Observability plane (tracing + a
    separate evaluation harness) — tracing is implemented and deployed
    (Phase 7); the evaluation harness remains a separate, not-yet-built
    piece, deliberately not conflated with tracing.
21. Databricks App is the Interface Layer (Layer 6) — its backend is
    implemented and deployed to `dev` (Phase 8, `POST /api/ask`); a
    chatbot UI beyond this API contract remains planned/future work, not
    owned by this backend.
22. Raw future client documents will use a separate, Volume-based
    ingestion pipeline, not Git.
23. External PDF ingestion is planned for **after** the end-to-end core
    RAG (Phases 1–6) is functional, not before.
24. The existing notebook/POC (Adriano) is a useful end-to-end
    implementation reference, not the production architecture — specific
    parts of it (PDF pipeline, fixed-size splitter, `rag.chunks`/
    `chunks_index`, `processed_files` + `DELETE`+`APPEND`, default
    `databricks-gte-large-en`) are explicitly not incorporated (see
    "Relationship to the Adriano POC").
25. `document_chunks_index` is not yet managed by the Asset Bundle / IaC —
    it was created directly via the Databricks API/CLI (see "Phase 3B —
    AI Search Infrastructure as Code").
26. Stable knowledge (business rules, metric definitions, methodology,
    architecture, governance guidance) flows through Markdown → AI Search;
    dynamic analytical/ML outputs flow through a separate Delta/SQL runtime
    query path — the two are never conflated (see "Structured Runtime Data
    — Analytical / ML Outputs (PLANNED)").
27. ML/analytical predictions are never converted to canonical Markdown,
    chunked, embedded, or synced into the AI Search index on every model
    run — `document_chunks`/`document_chunks_index` remain reserved for the
    six stable canonical documents.
28. A single user question may legitimately require combining documentary
    retrieval (AI Search) and structured data (Delta/SQL) evidence before
    generation — this is an explicit, planned orchestration case, not an
    edge case to avoid.
29. Power BI semantic rules belong to the stable-knowledge/Markdown/AI
    Search path, never to the structured ML-output/Delta-SQL path — now
    promoted and runtime-validated in that path, not merely planned.
30. "ML outputs" (predictive ML data queried via Delta/SQL) and "MLflow"
    (the Quality/Evaluation/Observability plane for the RAG application
    itself) are distinct concepts and must not be conflated — MLflow
    remains the transversal plane; it is not the mechanism by which
    prediction values reach the application.
31. The canonical corpus currently contains 6 documents / 163 chunks — not
    a target or an estimate, but the runtime-validated current state (see
    "Delta Runtime Validation Results").
32. The Power BI promotion runtime-proved incremental addition behavior: a
    new canonical document can be added via a scoped Delta `MERGE` that
    inserts only its own chunks while the pre-existing chunks of all other
    canonical documents remain byte-identical (same `chunk_id`,
    `content_hash`, `document_hash`, `ingested_at`) — this is now a second
    demonstrated runtime behavior alongside idempotent-rerun (see
    "Delta Runtime Validation Results").
33. Cross-source retrieval quality (a single query needing evidence from
    more than one canonical document, potentially across the
    Gold/dbt-curated and PBIP/TMDL-curated provenance paths) has now been
    evaluated empirically (Phase 4A.1/4C): cross-source queries need a
    larger candidate depth (Top-K=10) than single-source queries (Top-K=8)
    to reach complete evidence coverage — decided for the current policy,
    though the underlying sample is small (3 queries; see "Retrieval
    Quality — Known Limitations").
34. Evidence-level evaluation is authoritative for retrieval-quality
    claims in this project — a result matching only the expected
    *document*, without matching a specific Rule ID or section cue, is
    **not** sufficient to count as a retrieval success (Phase 4A.1).
    Document-level matching alone previously produced a false-success bug
    and is not used for Hit/Coverage/MRR again.
35. Exact Rule ID queries (`GOLD-\d{3}` and nothing else) use deterministic
    metadata routing — HYBRID + a `rule_ids` filter derived only from the
    query text, Top-K=5 (Phase 4C `EXACT_RULE_ID` route) — not plain
    HYBRID, which Phase 4B measured as systemically weak for this query
    shape (MRR=0.045 vs. 0.631 with the filter).
36. A syntactically valid but nonexistent Rule ID (e.g. `GOLD-999`) never
    falls back to semantic search — it returns zero results. An explicit
    identifier lookup must not silently substitute a semantically similar
    but incorrect rule.
37. Ordinary natural-language queries (no exact identifier, no explicit
    cross-source cue) stay on plain HYBRID — Phase 4B's findings for
    identifier queries were not generalized to this query class without
    separate evidence.
38. Deterministic query expansion for identifier queries (Phase 4B A3) is
    explicitly **not** part of the retrieval policy — it showed no
    meaningful improvement over plain HYBRID.
39. The Databricks reranker is **not** part of the current retrieval
    policy — not because it was found ineffective, but because it is
    `UNAVAILABLE_IN_CURRENT_ENVIRONMENT` (a workspace-level configuration
    block, confirmed via the query API's own error response). No
    infrastructure change was attempted to enable it.
40. Every retrieval policy decision exposes route/query_type/top_k/filter/
    extracted-Rule-ID metadata (`RetrievalPolicyDecision`) — this now
    feeds `harness/tracing.py`'s request-level MLflow span attributes
    (Phase 7), and is exposed through `HarnessResult`/`app.py` for Harness
    debugging (Phase 6A/8).
41. LlamaIndex coordinates execution only — it does not decide retrieval
    policy. `orchestration/rag_pipeline.py` always obtains its routing
    decision from `src.retrieval.policy.classify_query` (or an explicitly
    passed-in `RetrievalPolicyDecision`, e.g. from a caller such as
    `AIHarness`); it never invents its own classification (Phase 5A).
    `AIHarness.ask` (Phase 6A) does not currently exercise this override
    path — it calls `RagOrchestrator.generate` the same way any other
    caller does, without passing an explicit decision.
42. A custom LlamaIndex `BaseRetriever` (`PolicyRetriever`) wraps the
    existing `ai_search.py` client rather than adopting the official
    `DatabricksVectorSearch` LlamaIndex integration — chosen specifically
    to guarantee the already-validated HYBRID/`rule_ids`-filter/Top-K/
    metadata semantics are preserved exactly, not to avoid the official
    integration on principle.
43. Databricks Llama generation lives under `generation/`
    (`llama_client.py`), not under `orchestration/` — "how the model is
    called" is a generation-layer concern; `orchestration/rag_pipeline.py`
    depends only on the plain LlamaIndex `CustomLLM` interface.
44. Current orchestration is intentionally consolidated into one module,
    `orchestration/rag_pipeline.py`, rather than kept as the six separate
    files Phase 5A initially produced — real-usage inspection (the
    Architecture Scope Review) found no external consumer of any of those
    files, so the separation added no value at this project's size.
45. The speculative `structured_data.py` abstraction (a `Protocol` with no
    implementation and no caller) was removed, not merely deferred in
    place. A real structured-data implementation was later introduced in
    Phase 6B.1 (`src/structured/`, generalized to Structured Analytics) for a real data
    contract; the ML/forecast side still waits for its own contract.
46. `orchestration/rag_pipeline.py` always returns deterministic source
    metadata (`S1`, `S2`, ... with chunk/document/section/rule-ID/score/
    rank) alongside the model's answer, independent of what the model
    itself cites in prose — this metadata supports citation validation,
    Harness checks, and MLflow tracing (see items 48-50).
47. No generation call is made when retrieval returns zero evidence (the
    Phase 5A empty-retrieval guard) — this is a narrow, deterministic
    guard against the "zero candidates" case specifically, not a general
    evidence-sufficiency judgment.
48. Citation validation is implemented (`evaluation/citations.py`,
    `check_citation_integrity`, Phase 6A/8) — the model's own `[Sn]`
    citation markers are now checked against the deterministic `sources`
    metadata (see the Case C observation in "Runtime validation," under
    "Orchestration Layer — LlamaIndex", which this addresses). It is a
    **structural** check only (label existence), not semantic
    groundedness — see "Response Verification / Groundedness".
49. MLflow tracing wraps `AIHarness.ask` from **outside**
    (`harness/tracing.py`'s `traced_ask`), never from inside `harness.py`
    — `AIHarness` has no `import mlflow` anywhere, keeping it fully
    observability-framework agnostic. Autologging
    (`mlflow.llama_index.autolog()`) already captures retrieval/generation
    spans with no manual instrumentation; the manual span this project
    adds is deliberately minimal (one parent span + a handful of
    Harness-level attributes autolog cannot know).
50. Citation validation lives in `evaluation/`, not in `harness/` — it is
    called by the Databricks App (`app.py`), not by `AIHarness.ask`
    itself, so the Harness stays focused on routing/validation/failure
    containment and citation checking stays reusable independent of any
    particular caller.
51. The Databricks App is a thin FastAPI shim: it calls `traced_ask()` and
    serializes the result — it does not call AI Search or the Llama
    serving endpoint directly, does not classify the query itself, and
    does not duplicate Harness/Orchestrator/Policy logic. The API request
    contract is frozen to `{"query": "..."}` for V1 — no `source_route`,
    `top_k`, or model-selection parameter is exposed to callers.
52. The App's deployed source layout points `source_code_path` at `rag/`
    (not `rag/app`) specifically so `rag/src/` deploys as a sibling of
    `app.py` inside the container, matching every existing script's
    `from src...`/`cwd=rag/` import convention — no RAG module is
    duplicated into a separate App directory.
53. Vector Search index access for an App's service principal is
    representable as a Bundle `uc_securable` resource
    (`securable_type: TABLE`, since Vector Search Delta Sync indexes are
    themselves UC `TABLE` securables) — but granting it via that Bundle
    path requires the deploying identity to hold `MANAGE`/`USE CATALOG`
    on the source catalog, which is not automatically true just because
    that identity can deploy the rest of the Bundle. **Historical/resolved:**
    this was a real, live-discovered permission gap at the time it was
    found — the App's service principal now holds `USE_CATALOG`,
    `USE_SCHEMA`, and `SELECT` on `document_chunks_index` (granted outside
    this Bundle path) and this has been exercised successfully by live
    `POST /api/ask` requests (see "Interface Layer — Databricks App"). The
    `uc_securable` Bundle mechanism itself remains a valid, documented
    option for declaratively managing this grant in the future, but is not
    currently what provides the access.

## Open Decisions

Genuinely unresolved items — not answered by this document:

- Evidence-sufficiency / OOD threshold — no score-based rule exists for
  deciding "this candidate set is sufficient to answer"; 3 of 4 negative
  queries score close to genuine positives (Phase 4A.1/4B).
- Reranking — remains unevaluated, not rejected; `UNAVAILABLE_IN_CURRENT_ENVIRONMENT`
  today, to be revisited only if the workspace configuration later permits it.
- Generic-heading mitigation (content restructuring in the Markdown) — not
  yet attempted; the Phase 4C policy changes Top-K per route, not what is
  returned within it.
- A broader cross-source evaluation dataset — the current `cross_source_top_k=10`
  decision rests on only 3 classified queries.
- Initial token budget for the context manager (distinct from
  retrieval-side Top-K, which Phase 4C now decides — see "Top-K vs Token
  Budget"; Phase 5A currently sends the full retrieved set with no budget
  selection).
- Harness-level prompt design (guardrails, citation enforcement, source
  routing awareness) — a minimal Phase 5A grounding prompt exists
  (`GROUNDING_PROMPT_TEMPLATE`, see "Generation Design") but is explicitly
  not the final Harness prompt.
- Evidence-sufficiency thresholds for response verification (Harness-level,
  distinct from the OOD retrieval-score question above, and distinct from
  Phase 5A's narrow empty-retrieval guard).
- Final raw-document Volume path.
- Document parser/extractor technology for the future PDF/DOCX pipeline.
- Quantitative auto-approval thresholds (what counts as "plausible text
  coverage," which sources are "trusted," etc.).
- Final `document_registry` schema and other document-registry details.
- Concrete implementation of document retirement.
- Which advanced retrieval metrics to adopt for future benchmarking, and
  target thresholds (beyond the Phase 4A.1 baseline metrics already
  measured).
- AI Search Infrastructure-as-Code representation of `document_chunks_index`
  (see "Phase 3B — AI Search Infrastructure as Code").
- Concrete ML structured table/view contract for analytical/prediction
  outputs (see "Structured Runtime Data — Analytical / ML Outputs
  (PLANNED)" and "Phase 6B" — currently CONCEPTUAL / OPEN, no schema
  defined; the previous `StructuredEvidence`/`StructuredDataProvider`
  placeholder interface was removed during the Lean Core review, not kept
  as a stand-in for this).
- Structured-query interface for ML/forecast outputs (Gold-layer
  analytics have one since Phase 6B.1 — `StructuredQueryService` and the
  Structured Analytics registry — but ML/forecast outputs still need a real
  data contract).
- Routing rules between AI Search and structured data beyond the approved
  metrics: Phase 6B.1 ships a deterministic rule-based router
  (`AI_SEARCH` / `STRUCTURED_DATA` / `BOTH`, owned by the AI Harness;
  distinct from the Phase 4C retrieval policy, which only routes within AI
  Search). Whether a semantic router is ever warranted, and how to route
  further intents, remains open.
- External-browser CORS/authentication integration path for a future
  chatbot UI running outside Databricks (see `rag/API_CONTRACT.md`'s
  "Authentication / Integration" section) — same-origin use inside
  Databricks is unaffected and does not need this resolved first.

**Resolved since the previous revision of this document** (kept out of the
list above; see the relevant sections for evidence): whether to use
LlamaIndex for orchestration (yes — implemented, Phase 5A); whether the
end-to-end orchestration path works (yes —
`ORCHESTRATION_RUNTIME_VALIDATED`, 5 live smoke calls); whether the
Databricks Llama endpoint works (yes — `databricks-meta-llama-3-3-70b-instruct`,
confirmed `READY` and runtime-validated); whether the official LlamaIndex
`DatabricksVectorSearch` integration must be used (no — a custom retriever
adapter was used instead, to guarantee the already-validated retrieval
semantics are preserved exactly); the physical orchestration module
structure (consolidated into one `rag_pipeline.py`, see the Architecture
Scope Review); whether `structured_data.py` should remain as a
placeholder now (no — removed, deferred until a real contract exists,
Phase 6B). Also previously resolved: default `NORMAL` Top-K (8) and
`CROSS_SOURCE` Top-K (10), derived from the Phase 4A.1 offline analysis;
exact-identifier query behavior and its nonexistent-ID case (Phase 4C
`EXACT_RULE_ID` route — zero results, no fallback); whether plain HYBRID
is adequate for identifier queries (no — Phase 4B, MRR=0.045 vs. 0.631
with a `rule_ids` filter); deterministic query expansion for identifiers
(rejected — no meaningful improvement, Phase 4B A3); current rule-ID
routing behavior (HYBRID + `rule_ids` filter, Top-K=5); the fix for the
`run_ingestion.py` `spark_python_task` entrypoint; Qwen3 managed-embedding
compatibility with AI Search; whether the index's sync `pipeline_type` is
`TRIGGERED` or `CONTINUOUS` (`TRIGGERED`, confirmed working); whether a
sixth canonical document can be added without rewriting the existing
corpus (yes — runtime-proved, see "Delta Runtime Validation Results"); and
the Power BI promotion itself (complete — `powerbi_semantic_rules.md` is
canonical, ingested, indexed, and retrieval-validated).

**Also resolved since Phase 6A/7/8:** the final citation schema (resolved
— `check_citation_integrity`'s `VALID`/`INVALID`/`NO_CITATIONS`, a
structural label-existence check, see item 48 above); whether the AI
Harness needs its own MLflow import (no — tracing wraps it from outside,
item 49); where citation validation should live (in `evaluation/`, called
by the App, not inside `harness.py`, item 50); the deployable App source
layout (`source_code_path: rag`, not `rag/app`, item 52); whether
`mlflow-tracing` alone is sufficient for the deployed App (no — it passes
in an isolated venv but crashes the real App container; the full `mlflow`
package is required, see "Interface Layer — Databricks App"); whether
Vector Search index access is representable in the Bundle schema at all
(yes, via `uc_securable`, `securable_type: TABLE`, item 53); whether the
App's service principal actually has sufficient Unity Catalog access to
serve real requests (yes — `USE_CATALOG`/`USE_SCHEMA`/`SELECT` all
confirmed held and exercised by live `GOLD-032`/`GOLD-999`/invalid-query
requests through the deployed HTTP endpoint); whether `databricks bundle
deploy` alone reliably restarts a crashed App process (no — a separate
`databricks bundle run <resource> -t dev --restart` was needed in the one
case observed, see "Interface Layer — Databricks App"); and whether the
full local test suite passes (yes — 1208 passed, 0 failed, 0 skipped after
the Structured Analytics additions, see "Testing").

Nothing above is prematurely resolved by this README.
