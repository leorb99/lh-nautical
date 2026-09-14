# RAG — Grupo 9

RAG (Retrieval-Augmented Generation) pipeline over the canonical Gold-layer
documentation of the LH Nautical project. Goal: grounded Q&A with citations
back to Rule IDs and source sections, over a small, hand-canonized Markdown
knowledge base.

## Status

| Component | Status |
|---|---|
| Knowledge Base | FINAL / CLOSED |
| Ingestion Contract | FINAL / CLOSED |
| `loader.py` | FINAL / CLOSED |
| `parser.py` | FINAL / CLOSED |
| `chunker.py` | FINAL / CLOSED |
| Embedding model selection (MVP) | CLOSED |
| Delta Persistence Design | FINAL / CLOSED |
| Persistence pure-Python components (`schema_contract.py`, `persistence_validation.py`) | IMPLEMENTED |
| Spark/Delta runtime components (`spark_schema.py`, `persistence.py`, `run_ingestion.py`) | IMPLEMENTED, NOT YET INTEGRATION-VALIDATED |
| Databricks integration (real Spark/Delta/Unity Catalog execution) | PENDING |
| Delta `document_chunks` table | NOT YET CREATED |
| Delta `MERGE` | NOT YET EXECUTED |
| Change Data Feed (runtime behavior) | NOT YET RUNTIME-VALIDATED |
| Databricks AI Search | PLANNED / NOT YET IMPLEMENTED |
| Retrieval | PLANNED / NOT YET IMPLEMENTED |
| Claude generation | PLANNED / NOT YET IMPLEMENTED |
| Power BI semantic rules | DEFERRED / NOT INGESTED |

## Architecture

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
[IMPLEMENTED, NOT YET RUN] persist_chunks()
  (validate -> 16-col source DataFrame -> validate target -> MERGE)
        |
        v
[NOT YET CREATED] Unity Catalog
  document_chunks (17 columns, CDF enabled)
        |
        v
[PLANNED] Databricks AI Search
  Delta Sync Index
        |
        v
  Qwen3-Embedding-0.6B
        |
        v
  Hybrid Search (vector + lexical)
        |
        v
      Top-K
        |
        v
  Claude search_result
        |
        v
Grounded answer + citations
```

Everything up to and including `Chunk[]` is implemented and tested. The
Delta Persistence layer's code (pure-Python validation/contracts and
Spark/Delta runtime code) is also implemented, but has not yet been run
against a real Databricks workspace — no `document_chunks` table exists yet
and no `MERGE` has been executed (see "Persistence Layer" below). AI
Search, retrieval, and generation are planned but not implemented.

## Canonical Knowledge Base

The authorized MVP corpus contains exactly five Markdown documents, all
`document_status = CLOSED`:

| File | `document_id` | `canonical_domain` |
|---|---|---|
| `rag/knowledge_base/gold_business_rules.md` | `gold_business_rules` | `business_rules` |
| `rag/knowledge_base/inventory_and_stockout.md` | `inventory_and_stockout` | `inventory_stockout` |
| `rag/knowledge_base/demand_forecasting.md` | `demand_forecasting` | `demand_forecasting` |
| `rag/knowledge_base/metrics_dictionary.md` | `metrics_dictionary` | `metrics` |
| `rag/knowledge_base/gold_data_architecture.md` | `gold_data_architecture` | `data_architecture` |

`loader.py` never discovers documents by scanning the directory — it reads
this exact allowlist. `powerbi_semantic_rules.md` exists in
`rag/knowledge_base/` but is **DEFERRED / NOT INGESTED**: it is not part of
`CANONICAL_DOCUMENTS` and generates no chunks in this phase. No PDF, PBIX,
PBIP, or other reference file is treated as a canonical source.

## Ingestion Pipeline

### Loader

`rag/src/ingestion/loader.py` — FINAL / CLOSED.

- Explicit allowlist of the 5 canonical documents (`CANONICAL_DOCUMENTS`);
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

Computed from a real run of loader → parser → chunker over the 5 canonical
documents (not estimated):

| Metric | Value |
|---|---|
| Canonical documents | 5 |
| `ParsedSection`s | 117 |
| Heading-only sections skipped | 18 |
| Final retrieval chunks | 99 |
| Sections split (exceeded hard max) | 0 |
| Chunks above hard max | 0 |
| Overlap actually used | 0 |
| Lexical fallback actually used | 0 |

Per-document chunk count:

| Document | Chunks |
|---|---|
| `gold_business_rules` | 17 |
| `inventory_and_stockout` | 19 |
| `demand_forecasting` | 12 |
| `metrics_dictionary` | 29 |
| `gold_data_architecture` | 22 |
| **Total** | **99** |

No section in the current corpus is large enough to trigger internal
splitting, overlap, or the lexical fallback path — those code paths are
implemented and tested, but not exercised by the current 5-document corpus.

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
and an empty `ARRAY<STRING>` in the future Delta table — never `NULL`.

## Persistence Layer

Delta Persistence Design is FINAL / CLOSED. Implementation is complete on
the pure-Python side and written (but not yet run) on the Spark/Databricks
side — no Databricks execution has happened yet.

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
`python_file` as a standalone script (confirmed by this repo's own
`tests/run_tests.py`, the sibling task in `resources/resources.yml`), never
as a package module, so `run_ingestion.py` cannot reach its sibling project
modules via relative imports the way `persistence.py`/`spark_schema.py`
reach *their* own siblings. `run_ingestion.py` instead locates the `rag/`
root from `__file__` (never the current working directory), inserts it
onto `sys.path`, and imports its own dependencies via the same absolute
`src.ingestion...` path the test suite already uses — the internal modules
it then loads keep their existing relative imports unchanged. It also sets
`sys.dont_write_bytecode = True` before those imports, mirroring
`tests/run_tests.py`'s own guard against the Databricks Workspace
filesystem's inability to write `__pycache__` (a failure mode this repo
has hit before). Actually running under a `spark_python_task` is a
required Databricks-only validation — see Roadmap.

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
integration concern, not yet run — see Roadmap below.

## Delta `document_chunks` Contract

Logical schema, defined in
[`rag/sql/create_document_chunks.sql`](./sql/create_document_chunks.sql)
(FINAL / CLOSED as a schema contract; not yet applied to a physical table):

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
KEY` is defined; `chunk_id` is intended to be used conceptually as the
primary key of the future Databricks AI Search Delta Sync index, which is
an index-level configuration, not a Delta table constraint.

**Change Data Feed:** `delta.enableChangeDataFeed = 'true'` is set on the
table contract, to support future incremental synchronization with
Databricks AI Search / Delta Sync. No AI Search endpoint or index is
created or configured yet.

**Physical namespace:** schema `rag` is closed (a dedicated schema,
sibling to the existing dbt-managed `bronze`/`silver`/`gold`, since
`document_chunks` is not part of that lineage). Catalog remains
environment-specific — never hard-coded as
`dev_<user>_lh_nautical`/`ci_lh_nautical`/`prod_lh_nautical` in Python or
SQL. Since Asset Bundle variable substitution (`${var.catalog_name}`,
`databricks.yml`) applies to YAML resource definitions, not to the literal
contents of a `.sql` file, both `rag/sql/create_schema.sql` and
`rag/sql/create_document_chunks.sql` instead use a Databricks named
parameter marker, `:catalog`, combined with the `IDENTIFIER()` clause —
e.g. `CREATE TABLE IF NOT EXISTS IDENTIFIER(:catalog || '.rag.document_chunks') (...)`.
`:catalog` is a Databricks named parameter used only by bootstrap SQL — its
value is bound at execution time (e.g. `spark.sql(sql_text, args={"catalog": ...})`
or `EXECUTE IMMEDIATE ... USING <value> AS catalog`), never by editing the
file. The actual invocation of these files against a real catalog (the
Python bootstrap step that binds `:catalog`) is not yet implemented.

### Embedding storage

Do not manually add an embedding column to `document_chunks` while using
the Databricks-managed embedding path. The canonical Delta source stores
chunk text, metadata, hashes, and provenance. Embeddings are generated
later by Databricks AI Search / the configured embedding model, and are
not part of this table's contract.

## Embedding Model — MVP

**Primary model:** Qwen3-Embedding-0.6B.

| Property | Value |
|---|---|
| Foundation Model API endpoint | `databricks-qwen3-embedding-0-6b` |
| Databricks AI Functions identifier tested | `system.ai.qwen3-embedding-0-6b` |
| Workspace | Databricks Free Edition |
| Foundation Model access | **CONFIRMED EMPIRICALLY** |
| AI Search managed-embedding integration | PLANNED / TO BE VALIDATED |

Access was confirmed by executing, directly in the target workspace:

```sql
SELECT ai_query(
  'system.ai.qwen3-embedding-0-6b',
  'Como é calculado o estoque de segurança?'
) AS embedding;
```

Result: an embedding vector was returned successfully. This confirms
Foundation Model access to Qwen3-Embedding-0.6B in this Free Edition
workspace. It does **not** confirm that a Databricks AI Search Delta Sync
index using Qwen3 as its managed embedding model works end-to-end — no AI
Search endpoint or index has been created or tested yet; that remains
PLANNED.

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

No dependency on either alternative has been introduced.

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
- Qwen3-Embedding-0.6B access was confirmed empirically via `ai_query` (see
  above).
- Databricks AI Search usage must stay within Free Edition limits.
- The architecture avoids depending on paid-tier-only resources for the
  MVP critical path.

No specific quotas or numeric limits are documented here beyond what is
already established above, to avoid stating figures without confirmed
evidence.

## Planned AI Search Architecture

Not yet implemented. Planned shape, once Delta Persistence exists:

- Databricks AI Search Delta Sync Index over `document_chunks`, keyed
  conceptually by `chunk_id`.
- Databricks-managed embedding via Qwen3-Embedding-0.6B.
- Hybrid retrieval (vector + lexical/keyword), to let the lexical component
  carry weight for exact-identifier queries (`GOLD-025`, `fct_sales`,
  `avg_daily_sales_90d`) alongside semantic queries.
- Top-K results passed as `search_result` context to Claude for a grounded
  answer with citations.

None of this is created or configured yet.

## Testing

Local pure-Python test suite, last validated (executed) run:

```
202 passed, 0 failed
```

- `test_loader.py` + `test_parser.py` + `test_chunker.py`: 153 (unchanged;
  includes the 12 heading-only-section scenarios and 6
  indivisible-lexical-segment scenarios from the retrieval-quality
  micro-patch, plus persistent validation against all 5 real canonical
  documents).
- `test_schema_contract.py`: 22 (field counts, order, `ingested_at`
  presence/absence, uniqueness, physical nullability, `rule_ids` array
  metadata, allowed logical types, source/target field-set difference).
- `test_persistence_validation.py`: 27 (every pre-write validation rule,
  including the zero-chunk-batch and 5-real-document/99-chunk end-to-end
  cases).
- `test_persistence_expressions.py`: 27 tests added for the extracted
  pure-Python MERGE-expression module (identifier parsing, SQL literal
  escaping, matched-update condition/set, insert mapping, scoped-delete
  condition). Syntax-verified (`python -m py_compile`); not yet executed
  in this environment (pytest was unavailable and, per the instructions
  for that patch, was deliberately not installed) — pending confirmation
  in the next run where `pytest` is available.

No PySpark, `delta-spark`, or Java is installed or used anywhere in this
suite. `spark_schema.py`, `persistence.py`, and `run_ingestion.py` compile
syntactically (`python -m py_compile`) but are not imported or executed
locally — real Spark/Delta/`MERGE`/CDF behavior is a Databricks integration
concern, not yet validated (see Roadmap).

## Provenance

`rag/` may currently be untracked in the working tree. In that case,
`source_git_commit = NULL` for every loaded document is expected and
correct — not a bug (see the Loader section above for the exact rule).

Before any final/official ingestion for demonstration purposes, the
canonical files and pipeline code should preferably be versioned/committed
so that `source_git_commit` can reflect the actual ingested commit. No
commit is made as part of this documentation update.

An idempotent rerun (see Roadmap — "Identical rerun: expect 0 inserts, 0
updates, 0 deletes") requires the same canonical content **and** the same
Git provenance state across both runs. A change from untracked/dirty
content to a committed version between runs can legitimately change
`source_git_commit` from `NULL` to a real SHA — that is expected
provenance metadata drift (see "Persistence Layer" — matched update
fields), not a failure of idempotency.

## Roadmap / Next Step

**Delta Persistence Design and its pure-Python implementation are closed.**
`Chunk` → DataFrame conversion and `MERGE` semantics are designed and
implemented (see "Persistence Layer" above) — what remains is exclusively
Databricks integration validation, not further design:

- Bind `:catalog` to a real environment catalog value (e.g. via
  `spark.sql(sql_text, args={"catalog": ...})`) and run
  `create_schema.sql` / `create_document_chunks.sql` against an actual
  Databricks Free Edition workspace (table does not exist yet). Confirm
  the actual resolved `dev` catalog name (from `${var.catalog_name}` /
  `${workspace.current_user.short_name}`) contains only characters
  accepted by `persistence_expressions.parse_three_part_name`'s
  conservative identifier regex — flagged as an open runtime check, not
  assumed either way (see below).
- Confirm `run_ingestion.py` actually imports and runs correctly under a
  real `spark_python_task` direct-script execution.
- `validate_target_table`'s `information_schema.columns`-based checks,
  against a real Unity Catalog table.
- `chunks_to_dataframe` / `build_chunk_source_schema` DataFrame
  construction against a live `SparkSession`.
- First real `MERGE` run: 5 documents, expect 99 inserts, 0 updates, 0
  deletes.
- Identical rerun: expect 0 inserts, 0 updates, 0 deletes (idempotency).
- Scoped update/delete behavior on a changed document.
- Zero-chunk processed document against an empty-but-schema-correct source
  DataFrame — a documented **design expectation**, not yet empirically
  confirmed (official Delta `MERGE` docs do not explicitly state this
  case's behavior).
- `operationMetrics` / `DESCRIBE HISTORY` retrieval shape, and CDF event
  inspection (insert / update_preimage+postimage / delete records; zero
  records on a no-op rerun).
- Wiring `run_ingestion.py` into a Databricks Asset Bundle job/task
  (`resources/resources.yml` is not modified by this document).

**Persistence principles (CLOSED, implemented):**

- No blind append — a single scoped `MERGE`, not `df.write.mode("append")`.
- Writes are idempotent by design (matched-but-unchanged rows are never
  written to, so `ingested_at` never updates without a real change).
- Stale chunks from a reingested/changed document are removed via a
  `processed_document_ids`-scoped delete, never an unscoped one.
- Validation (`validate_chunks_for_persistence`) always happens before any
  write — `persist_chunks` cannot be called in a way that bypasses it.
- `ingested_at` belongs to the persistence layer (generated at `MERGE`
  time), never to chunk identity.

Volume: still **NOT required** before first ingestion — Git remains the
canonical source; `rag/sql/create_volume.sql` is unchanged and deferred.
AI Search, retrieval, and Claude integration remain PLANNED and out of
scope until the Databricks integration items above are validated.
