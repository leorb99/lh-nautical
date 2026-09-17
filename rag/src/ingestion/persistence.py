"""Delta persistence runtime for the RAG document_chunks table.

Databricks/Spark-runtime-only module. Importing this module requires
PySpark and the Databricks-Runtime-provided ``delta`` package — it is not
required to be importable locally without PySpark, and it is NOT imported
by any local pure-Python test in this project.

Pure-Python expression building (identifier parsing, SQL literal escaping,
the MERGE update/insert/delete expressions) lives in
``persistence_expressions.py``, which this module imports rather than
redeclaring — see that module for the closed MERGE semantics themselves.

Responsibilities (see ``rag/README.md`` — Delta Persistence Design, and the
alignment/review reports it was built from):

1. validate a ``Chunk`` batch via ``persistence_validation.
   validate_chunks_for_persistence`` BEFORE any write is attempted;
2. convert the validated batch into the 16-column source DataFrame
   (``chunks_to_dataframe``, via ``spark_schema.build_chunk_source_schema``);
3. confirm the 17-column target table exists and is schema-compatible
   (``validate_target_table``) — this module never creates or alters the
   target; physical DDL ownership belongs to
   ``rag/sql/create_schema.sql``/``rag/sql/create_document_chunks.sql``;
4. execute a single, atomic, scoped Delta MERGE (insert + meaningful
   update + processed-document-scoped stale delete);
5. return a best-effort ``PersistenceResult`` derived from Delta's own
   documented ``operationMetrics`` — observability only, never used for
   correctness decisions anywhere in this module or its tests.

``persist_chunks`` is the only entry point callers should use — it always
validates before writing, so there is no way to reach the target table with
an unvalidated batch through this module's public API.

Not executed as part of this implementation task. No SparkSession is
created here; the caller (``run_ingestion.py``) supplies one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from delta.tables import DeltaTable
from pyspark.sql import DataFrame, Row, SparkSession

from .chunker import Chunk
from .persistence_expressions import (
    PersistenceTargetError,
    build_delete_condition,
    build_insert_values,
    build_update_condition,
    build_update_set,
    parse_three_part_name,
    sql_string_literal,
)
from .persistence_validation import validate_chunks_for_persistence
from .schema_contract import (
    CHUNK_SOURCE_FIELDS,
    DOCUMENT_CHUNKS_FIELDS,
    field_names,
    physical_not_null_field_names,
)
from .spark_schema import build_chunk_source_schema

__all__ = [
    "PersistenceTargetError",
    "PersistenceMetricsError",
    "PersistenceResult",
    "chunks_to_dataframe",
    "validate_target_table",
    "persist_chunks",
]


class PersistenceMetricsError(ValueError):
    """Raised when Delta's operationMetrics do not contain an expected key,
    or when the most recent DESCRIBE HISTORY row is not a MERGE, or when
    the derived ``unchanged`` count is negative.

    Deliberately not silenced/defaulted in any of these cases — an
    inconsistent or missing metric means the PersistenceResult cannot be
    honestly reported, not that it should be fabricated or clamped.
    """


# ---------------------------------------------------------------------------
# PersistenceResult — observability / execution summary only.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PersistenceResult:
    """Best-effort summary of one MERGE, derived from Delta's own documented
    operationMetrics (DESCRIBE HISTORY). NOT a correctness mechanism — tests
    must verify outcomes directly against target-table state, never solely
    via these numbers (see the Delta Persistence Alignment Report: a known
    Delta Lake issue has reported numTargetRowsInserted over-counting under
    certain conditions).

    ``unchanged`` is not itself a Delta-reported metric; it is derived
    arithmetically from officially documented ones
    (``numSourceRows - inserted - updated``), never fabricated, and never
    silently clamped if the arithmetic goes negative — see
    ``_read_persistence_result``.
    """

    inserted: int
    updated: int
    deleted: int
    unchanged: int


# ---------------------------------------------------------------------------
# Chunk -> 16-column source DataFrame.
# ---------------------------------------------------------------------------


def chunks_to_dataframe(spark: SparkSession, chunks: Sequence[Chunk]) -> DataFrame:
    """Convert a validated ``Chunk`` sequence into the 16-column source
    DataFrame, using ``build_chunk_source_schema()`` explicitly — no schema
    inference. Never adds ``ingested_at``. Field-by-name, never by target
    column position (see schema_contract.py's note on differing field
    order between source and target).

    Works correctly for an empty ``chunks`` sequence (zero-row, correctly
    schema'd DataFrame) — no special branch is needed for the zero-chunk
    processed-document case; that is handled entirely by the scoped MERGE
    delete clause downstream.
    """
    schema = build_chunk_source_schema()
    names = field_names(CHUNK_SOURCE_FIELDS)
    rows = [
        Row(
            **{
                name: (list(getattr(chunk, name)) if name == "rule_ids" else getattr(chunk, name))
                for name in names
            }
        )
        for chunk in chunks
    ]
    return spark.createDataFrame(rows, schema=schema)


# ---------------------------------------------------------------------------
# Target table validation — VALIDATION ONLY. Never creates/alters/drops.
# ---------------------------------------------------------------------------


def validate_target_table(spark: SparkSession, target_table: str) -> None:
    """Confirm ``target_table`` exists and matches the 17-column
    DOCUMENT_CHUNKS_FIELDS contract exactly: field names, order, mapped
    types, array element type, and physical nullability (including the
    physical NOT NULL set). Fails fast on any mismatch. Never issues
    CREATE/ALTER/DROP/REPAIR — table creation is owned by
    ``rag/sql/create_schema.sql``/``rag/sql/create_document_chunks.sql``.

    Uses INFORMATION_SCHEMA.COLUMNS (a structured, documented Unity Catalog
    system table) rather than parsing DESCRIBE TABLE EXTENDED / SHOW CREATE
    TABLE text output, per the closed schema-validation strategy.
    """
    catalog, schema, table = parse_three_part_name(target_table)

    info_df = spark.sql(
        f"SELECT column_name, ordinal_position, data_type, is_nullable "
        f"FROM {catalog}.information_schema.columns "
        f"WHERE table_schema = {sql_string_literal(schema)} "
        f"AND table_name = {sql_string_literal(table)} "
        f"ORDER BY ordinal_position"
    )
    rows = info_df.collect()
    if not rows:
        raise PersistenceTargetError(
            f"Target table {target_table!r} does not exist or is not visible via "
            f"{catalog}.information_schema.columns."
        )

    expected_names = field_names(DOCUMENT_CHUNKS_FIELDS)
    actual_names = tuple(r["column_name"] for r in rows)
    if actual_names != expected_names:
        raise PersistenceTargetError(
            f"Target table {target_table!r} column names/order do not match the closed "
            f"DOCUMENT_CHUNKS_FIELDS contract.\nExpected: {expected_names}\nActual:   "
            f"{actual_names}"
        )

    expected_not_null = set(physical_not_null_field_names(DOCUMENT_CHUNKS_FIELDS))
    for row in rows:
        name = row["column_name"]
        is_nullable = row["is_nullable"]  # 'YES' / 'NO' per INFORMATION_SCHEMA convention
        expected_nullable = name not in expected_not_null
        actual_nullable = is_nullable == "YES"
        if actual_nullable != expected_nullable:
            raise PersistenceTargetError(
                f"Target table {target_table!r} column {name!r}: expected "
                f"nullable={expected_nullable} per the physical NOT NULL contract, "
                f"but INFORMATION_SCHEMA reports is_nullable={is_nullable!r}."
            )
    # data_type string comparison (e.g. "ARRAY" vs our "array<string>") is
    # deliberately left to the Databricks integration test tier: confirmed
    # via official Databricks documentation that INFORMATION_SCHEMA.COLUMNS
    # reports data_type for an ARRAY<STRING> column as the simplified string
    # "ARRAY" (the full spec lives in full_data_type instead) — asserting an
    # unverified format here risked a false fail-fast on real, compatible
    # data. Name/order/nullability are already enforced above without that
    # risk.


# ---------------------------------------------------------------------------
# MERGE — closed semantics, expressions built by persistence_expressions.py.
# ---------------------------------------------------------------------------


def _run_merge(
    spark: SparkSession,
    source_df: DataFrame,
    processed_document_ids: Sequence[str],
    target_table: str,
) -> None:
    """Single atomic Delta MERGE: matched update + explicit-named insert +
    processed-document-scoped not-matched-by-source delete. One
    ``.execute()`` call — no separate DELETE/APPEND statements.

    Uses the DeltaTable Python API (``delta.tables.DeltaTable``), which is
    provided by the Databricks Runtime without a local ``delta-spark``
    install. ``whenNotMatchedBySourceDelete`` (and the whole
    ``WHEN NOT MATCHED BY SOURCE`` DeltaMergeBuilder surface) is confirmed,
    per current official Databricks documentation, to require Databricks
    Runtime 12.2 LTS or above — any current Databricks Free Edition runtime
    exceeds this, so the method is safe to rely on here.

    Empty ``source_df`` (a processed document that legitimately produced
    zero chunks) is NOT special-cased: this same code path handles it,
    since ``whenNotMatchedBySourceDelete`` evaluates against target rows
    regardless of source row count. This is a DESIGN EXPECTATION pending a
    mandatory Databricks integration test (official Delta docs do not
    explicitly state empty-source MERGE behavior) — not yet empirically
    validated.
    """
    delta_table = DeltaTable.forName(spark, target_table)

    (
        delta_table.alias("target")
        .merge(source_df.alias("source"), "target.chunk_id = source.chunk_id")
        .whenMatchedUpdate(condition=build_update_condition(), set=build_update_set())
        .whenNotMatchedInsert(values=build_insert_values())
        .whenNotMatchedBySourceDelete(condition=build_delete_condition(processed_document_ids))
        .execute()
    )


# ---------------------------------------------------------------------------
# operationMetrics -> PersistenceResult. Observability only.
# ---------------------------------------------------------------------------


def _read_persistence_result(spark: SparkSession, target_table: str) -> PersistenceResult:
    """Build a PersistenceResult strictly from the most recent MERGE's
    officially documented operationMetrics — ``numTargetRowsInserted``,
    ``numTargetRowsUpdated``, ``numTargetRowsDeleted``, ``numSourceRows``.

    Two guards before trusting that row at all:

    1. the latest DESCRIBE HISTORY row's ``operation`` must literally be
       ``"MERGE"`` — DESCRIBE HISTORY is documented to return rows in
       reverse chronological order, so LIMIT 1 is normally the MERGE this
       call just ran, but this is a single-writer MVP, not a guarantee;
       reading metrics from an unrelated operation (e.g. a manual OPTIMIZE
       run concurrently) must fail loudly, not silently.
    2. ``unchanged`` (derived, not a reported metric) must not be negative
       — a negative value would indicate the underlying metrics are
       inconsistent (see the known numTargetRowsInserted over-counting
       issue), and is reported as an error rather than silently clamped
       or returned as-is.

    Nothing here participates in persist_chunks' write/control flow — the
    MERGE has already completed by the time this runs. Databricks
    integration tests validate correctness directly against the target
    table's actual row state, not against this result.
    """
    history_df = spark.sql(f"DESCRIBE HISTORY {target_table} LIMIT 1")
    rows = history_df.collect()
    if not rows:
        raise PersistenceMetricsError(
            f"DESCRIBE HISTORY {target_table} returned no rows immediately after a MERGE; "
            "cannot report a PersistenceResult."
        )
    latest = rows[0]

    operation = latest["operation"]
    if operation != "MERGE":
        raise PersistenceMetricsError(
            f"Most recent DESCRIBE HISTORY row for {target_table!r} has operation "
            f"{operation!r}, not 'MERGE'; refusing to interpret its operationMetrics as "
            "this MERGE's result. This can happen if another operation ran concurrently "
            "against the target table."
        )

    metrics = latest["operationMetrics"] or {}

    try:
        inserted = int(metrics["numTargetRowsInserted"])
        updated = int(metrics["numTargetRowsUpdated"])
        deleted = int(metrics["numTargetRowsDeleted"])
        num_source_rows = int(metrics["numSourceRows"])
    except KeyError as exc:
        raise PersistenceMetricsError(
            f"Expected Delta MERGE operationMetrics key missing: {exc}. Refusing to "
            "fabricate a PersistenceResult value — see module docstring."
        ) from exc

    # Every source row is either an insert (not matched) or a match (either
    # updated or left alone); derived arithmetically, not a separate
    # Delta-reported metric.
    unchanged = num_source_rows - inserted - updated
    if unchanged < 0:
        raise PersistenceMetricsError(
            f"Derived unchanged count is negative ({unchanged}) for {target_table!r}: "
            f"numSourceRows={num_source_rows}, numTargetRowsInserted={inserted}, "
            f"numTargetRowsUpdated={updated}. This indicates the underlying Delta "
            "operationMetrics are inconsistent (a known Delta Lake reliability caveat — "
            "see PersistenceResult's docstring); refusing to report a nonsensical value."
        )

    return PersistenceResult(inserted=inserted, updated=updated, deleted=deleted, unchanged=unchanged)


# ---------------------------------------------------------------------------
# Public entry point.
# ---------------------------------------------------------------------------


def persist_chunks(
    spark: SparkSession,
    chunks: Sequence[Chunk],
    processed_document_ids: Sequence[str],
    target_table: str,
) -> PersistenceResult:
    """Validate, convert, and persist one batch of chunks via a single
    scoped Delta MERGE.

    Always validates first — there is no way to reach the target table
    through this function with an unvalidated batch.

    Flow: validate_chunks_for_persistence -> chunks_to_dataframe ->
    validate_target_table -> MERGE -> PersistenceResult.
    """
    validate_chunks_for_persistence(chunks, processed_document_ids)
    source_df = chunks_to_dataframe(spark, chunks)
    validate_target_table(spark, target_table)
    _run_merge(spark, source_df, processed_document_ids, target_table)
    return _read_persistence_result(spark, target_table)
