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
fix is to locate the ``rag/`` root and put it on ``sys.path``, then import
this file's own project dependencies via the same absolute
``src.ingestion...`` path the test suite already uses (pytest's own
rootdir-insertion algorithm resolves imports the same way — see
rag/tests/*.py). Once ``rag/`` is on ``sys.path``, importing
``src.ingestion.persistence`` loads it as a member of the
``src.ingestion`` package, so its internal ``from .chunker import ...``
continues to resolve correctly as a sibling import — nothing inside that
module needs to change.

**Confirmed runtime behavior (not the originally assumed one):** a
Databricks ``spark_python_task`` does not execute its ``python_file`` as a
real ``python file.py`` subprocess. It runs
``exec(compile(source, filename, 'exec'))`` inside an IPython kernel
context, which never defines ``__file__`` in the execution namespace —
reproduced identically (``NameError: name '__file__' is not defined``)
across multiple independent job submissions. The ``rag/`` root discovery
below therefore never *relies* on ``__file__`` being present: it uses
``Path(__file__).resolve().parent`` as a search anchor when available
(local execution, ``python -m``, etc. — identical anchor to what this file
used before), and falls back to ``Path.cwd()`` when it is not (the
observed Databricks case). Either anchor is only a starting point — the
actual root is confirmed structurally (the first ancestor directory that
contains ``knowledge_base/``, ``src/``, and ``sql/`` together) before
anything is added to ``sys.path``, so neither path is ever trusted
blindly, and no arbitrary/hard-coded path (workspace user, bundle deploy
path, catalog, branch) is used.

This same bootstrap is harmless and idempotent if this file is ever
instead invoked as a module (e.g. ``python -m src.ingestion.run_ingestion``
with ``rag/`` as the working directory): the path is inserted at most
once, and the absolute import still succeeds.
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


def _find_rag_root(start: Path) -> Path:  # noqa: E402
    """Walk upward from ``start`` (inclusive) for the first directory that
    structurally looks like ``rag/`` — i.e. directly contains
    ``knowledge_base/``, ``src/``, and ``sql/`` as subdirectories.

    Never guesses: raises ``RuntimeError`` with an actionable message if no
    such directory is found, rather than falling back to an arbitrary path.
    """
    for candidate in (start, *start.parents):
        if (
            (candidate / "knowledge_base").is_dir()
            and (candidate / "src").is_dir()
            and (candidate / "sql").is_dir()
        ):
            return candidate
    raise RuntimeError(
        f"Could not locate the rag/ root by walking up from {start!r}: no "
        "ancestor directory contains knowledge_base/, src/, and sql/ "
        "together. Refusing to guess a path — confirm this script is "
        "running from within a checked-out copy of the repository (e.g. "
        "the Databricks Asset Bundle sync location)."
    )


try:
    _search_anchor = Path(__file__).resolve().parent
except NameError:
    # Observed Databricks spark_python_task behavior: __file__ is not
    # defined in this execution context (see module docstring). Fall back
    # to the current working directory as the search anchor only —
    # _find_rag_root still requires structural confirmation before
    # trusting it, exactly as in the __file__ case.
    _search_anchor = Path.cwd()

_RAG_ROOT = _find_rag_root(_search_anchor)
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
    # The repository root is rag/'s own parent, matching loader.py's
    # expectation that CANONICAL_DOCUMENTS' relative paths are resolved
    # against it. Derived from _RAG_ROOT (already structurally confirmed
    # above, via __file__ or Path.cwd()) rather than a second __file__
    # access, so this does not reintroduce the same failure mode.
    repo_root = _RAG_ROOT.parent
    result = run_ingestion(spark, repo_root, args.catalog, args.document_ids)
    print(result)


if __name__ == "__main__":
    main()
