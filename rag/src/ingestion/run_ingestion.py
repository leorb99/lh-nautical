"""Runtime entrypoint: load -> parse -> chunk -> persist the RAG canonical
corpus into ``<catalog>.rag.document_chunks``.

Databricks/Spark-runtime-only script (imports ``persistence.py``, which
requires PySpark/Delta). Not executed as part of this implementation task.

Direct-script execution compatibility: a Databricks ``spark_python_task``
runs its ``python_file`` as a standalone script (confirmed by this repo's
own existing ``tests/run_tests.py``, the sibling task in
``resources/resources.yml``), never as a package module. A standalone
script has no ``__package__`` context, so this file's sibling project
modules cannot be reached via relative imports (``from .chunker import
...``) the way ``persistence.py``/``spark_schema.py``/etc. reach *their*
own siblings — those modules keep their existing relative imports
unchanged; only this outermost entrypoint needs different treatment. The
fix here is minimal: locate the ``rag/`` root from ``__file__`` (never the
current working directory), put it on ``sys.path``, and import this
file's own project dependencies via the same absolute ``src.ingestion...``
path the test suite already uses (pytest's own rootdir-insertion algorithm
resolves imports the same way — see rag/tests/*.py). Once ``rag/`` is on
``sys.path``, importing ``src.ingestion.persistence`` loads it as a member
of the ``src.ingestion`` package, so its internal ``from .chunker import
...`` continues to resolve correctly as a sibling import — nothing inside
that module needs to change.

This same bootstrap is harmless and idempotent if this file is ever
instead invoked as a module (e.g. ``python -m src.ingestion.run_ingestion``
with ``rag/`` as the working directory): ``__file__`` is still defined,
the path is inserted at most once, and the absolute import still succeeds.

Runtime validation of this fix (actually running under a Databricks
``spark_python_task``) is a required Databricks-only check — see
rag/README.md.
"""

from __future__ import annotations

import sys

# Databricks Workspace filesystem does not support writing __pycache__ —
# this repo has hit that failure before (see tests/run_tests.py's own
# identical guard). Must be set before any of this file's own project
# imports below, which is why it comes first, ahead of even the sys.path
# bootstrap.
sys.dont_write_bytecode = True

from pathlib import Path  # noqa: E402 - see sys.dont_write_bytecode note above

# rag/src/ingestion/run_ingestion.py -> parents[2] is rag/ itself. Anchored
# to __file__, never to the current working directory.
_RAG_ROOT = Path(__file__).resolve().parents[2]
if str(_RAG_ROOT) not in sys.path:
    sys.path.insert(0, str(_RAG_ROOT))

import argparse  # noqa: E402
from typing import Sequence  # noqa: E402

from pyspark.sql import SparkSession  # noqa: E402

from src.ingestion.chunker import Chunk, chunk_document  # noqa: E402
from src.ingestion.loader import CANONICAL_DOCUMENTS, load_canonical_document  # noqa: E402
from src.ingestion.parser import parse_document  # noqa: E402
from src.ingestion.persistence import PersistenceResult, persist_chunks  # noqa: E402

__all__ = ["run_ingestion", "main"]

_SCHEMA = "rag"
_TABLE = "document_chunks"


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--catalog",
        required=True,
        help="Unity Catalog catalog name for this environment (resolved from "
        "${var.catalog_name} at deploy time — never hard-coded here).",
    )
    parser.add_argument(
        "--document-id",
        action="append",
        dest="document_ids",
        default=None,
        help="Restrict ingestion to specific canonical document_id(s). Repeatable. "
        "Defaults to all 5 canonical documents.",
    )
    return parser.parse_args(list(argv))


def run_ingestion(
    spark: SparkSession,
    repo_root: Path,
    catalog: str,
    document_ids: Sequence[str] | None = None,
) -> PersistenceResult:
    """Process the requested canonical documents (default: all 5) and
    persist their chunks in a single scoped MERGE.

    ``document_ids`` lets a future caller safely process a subset (1, 3, or
    5 documents) — the resulting ``processed_document_ids`` list is always
    built explicitly from which documents were actually loaded/parsed/
    chunked in this run, never inferred from the resulting chunk list, so a
    document that legitimately produces zero chunks is still correctly
    scoped for stale-row deletion.
    """
    specs = CANONICAL_DOCUMENTS
    if document_ids is not None:
        wanted = set(document_ids)
        known_ids = {spec.document_id for spec in specs}
        unknown = wanted - known_ids
        if unknown:
            raise ValueError(f"Unknown document_id(s) requested: {sorted(unknown)}")
        specs = tuple(spec for spec in specs if spec.document_id in wanted)

    all_chunks: list[Chunk] = []
    processed_document_ids: list[str] = []
    for spec in specs:
        loaded = load_canonical_document(repo_root, spec.relative_path)
        parsed = parse_document(loaded)
        chunks = chunk_document(parsed)
        all_chunks.extend(chunks)
        processed_document_ids.append(spec.document_id)

    target_table = f"{catalog}.{_SCHEMA}.{_TABLE}"
    return persist_chunks(spark, all_chunks, processed_document_ids, target_table)


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    spark = SparkSession.builder.getOrCreate()
    # rag/src/ingestion/run_ingestion.py -> parents[3] is the repo root,
    # matching loader.py's expectation that CANONICAL_DOCUMENTS' relative
    # paths are resolved against the repository root. Distinct from
    # _RAG_ROOT above (parents[2] = rag/ itself, used for the sys.path
    # bootstrap).
    repo_root = Path(__file__).resolve().parents[3]
    result = run_ingestion(spark, repo_root, args.catalog, args.document_ids)
    print(result)


if __name__ == "__main__":
    main()
