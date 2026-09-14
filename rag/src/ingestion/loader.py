"""Loader for the RAG MVP ingestion pipeline.

Consumes the ingestion contract documented in ``rag/README.md`` and
``rag/sql/create_document_chunks.sql`` (status: FINAL / CLOSED). This module
does not redefine that contract; it implements it.

Responsibilities of this module:

- hold the explicit allowlist of canonical documents for the MVP;
- locate and read a canonical document relative to the repository root;
- normalize its text deterministically;
- compute ``document_hash`` (SHA-256 of the normalized text);
- determine ``source_git_commit`` conservatively;
- produce an immutable :class:`LoadedDocument`.

Explicitly OUT of scope for this module (see ``parser.py`` / ``chunker.py``):

- interpreting Markdown headings or hierarchy;
- extracting Rule IDs;
- producing chunks, chunk_id, chunk_index or content_hash.
"""

from __future__ import annotations

import hashlib
import subprocess
import unicodedata
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "CanonicalDocumentSpec",
    "CANONICAL_DOCUMENTS",
    "LoadedDocument",
    "AllowlistError",
    "CanonicalDocumentMissingError",
    "normalize_text",
    "compute_document_hash",
    "load_canonical_document",
    "load_all_canonical_documents",
]


# ---------------------------------------------------------------------------
# Canonical allowlist (rag/README.md — Canonical Sources / Fixed Conventions)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CanonicalDocumentSpec:
    """Static mapping entry for one allowlisted canonical document."""

    relative_path: str
    document_id: str
    canonical_domain: str
    document_status: str


_KNOWLEDGE_BASE_DIR = "rag/knowledge_base"

# Explicit allowlist. The loader never discovers ``.md`` files by scanning the
# directory — only documents listed here can be loaded. powerbi_semantic_rules.md
# is intentionally absent (deferred, per the ingestion contract).
CANONICAL_DOCUMENTS: tuple[CanonicalDocumentSpec, ...] = (
    CanonicalDocumentSpec(
        relative_path=f"{_KNOWLEDGE_BASE_DIR}/gold_business_rules.md",
        document_id="gold_business_rules",
        canonical_domain="business_rules",
        document_status="CLOSED",
    ),
    CanonicalDocumentSpec(
        relative_path=f"{_KNOWLEDGE_BASE_DIR}/inventory_and_stockout.md",
        document_id="inventory_and_stockout",
        canonical_domain="inventory_stockout",
        document_status="CLOSED",
    ),
    CanonicalDocumentSpec(
        relative_path=f"{_KNOWLEDGE_BASE_DIR}/demand_forecasting.md",
        document_id="demand_forecasting",
        canonical_domain="demand_forecasting",
        document_status="CLOSED",
    ),
    CanonicalDocumentSpec(
        relative_path=f"{_KNOWLEDGE_BASE_DIR}/metrics_dictionary.md",
        document_id="metrics_dictionary",
        canonical_domain="metrics",
        document_status="CLOSED",
    ),
    CanonicalDocumentSpec(
        relative_path=f"{_KNOWLEDGE_BASE_DIR}/gold_data_architecture.md",
        document_id="gold_data_architecture",
        canonical_domain="data_architecture",
        document_status="CLOSED",
    ),
)

_DOCUMENTS_BY_RELATIVE_PATH: dict[str, CanonicalDocumentSpec] = {
    doc.relative_path: doc for doc in CANONICAL_DOCUMENTS
}


class AllowlistError(ValueError):
    """Raised when a requested document is not part of the canonical allowlist."""


class CanonicalDocumentMissingError(FileNotFoundError):
    """Raised when an allowlisted document cannot be found on disk."""


@dataclass(frozen=True)
class LoadedDocument:
    """Immutable, provenance-carrying representation of one loaded document.

    Deliberately does NOT include ``document_title`` — that is derived from
    the Markdown H1 and belongs to the parser, not the loader.
    """

    document_id: str
    source_path: str
    canonical_domain: str
    document_status: str
    text: str
    document_hash: str
    source_git_commit: str | None


# ---------------------------------------------------------------------------
# Text normalization (closed strategy — reused later for chunk content_hash)
# ---------------------------------------------------------------------------

_UTF8_BOM = "﻿"


def normalize_text(raw_text: str) -> str:
    """Apply the ingestion contract's text-normalization strategy.

    Steps, in order:
      1. strip a leading UTF-8 BOM, if present;
      2. normalize Unicode to NFC;
      3. normalize line endings (CRLF -> LF, CR -> LF).

    Does NOT collapse whitespace, strip trailing spaces, remove blank lines,
    ``.strip()`` the document, change capitalization, or otherwise alter
    Markdown semantics. Real textual changes must remain detectable.
    """
    text = raw_text
    if text.startswith(_UTF8_BOM):
        text = text[len(_UTF8_BOM) :]
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


def compute_document_hash(normalized_text: str) -> str:
    """SHA-256 hex digest (lowercase, 64 chars) of the normalized text, UTF-8 encoded."""
    return hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Conservative source_git_commit resolution
# ---------------------------------------------------------------------------


def _run_git(repo_root: Path, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        check=False,
    )


def _resolve_source_git_commit(
    repo_root: Path, relative_path: str, normalized_text: str
) -> str | None:
    """Return the HEAD commit SHA only if the ingested content is provably
    identical to the tracked version of the file at HEAD.

    Returns ``None`` (never raises) for any condition that only means "no
    valid Git provenance for this ingestion": no ``git`` binary available,
    not a Git repository, file untracked, uncommitted/dirty changes, or the
    ingested content diverging from the HEAD version. Never falls back to a
    bare ``git rev-parse HEAD`` without validating content equality.
    """
    try:
        head_result = _run_git(repo_root, ["rev-parse", "HEAD"])
        if head_result.returncode != 0:
            return None
        head_sha = head_result.stdout.decode("utf-8", errors="strict").strip()
        if not head_sha:
            return None

        # File must be tracked at HEAD; untracked files never qualify.
        tracked_result = _run_git(
            repo_root, ["ls-files", "--error-unmatch", relative_path]
        )
        if tracked_result.returncode != 0:
            return None

        # Content actually recorded for this path at HEAD.
        show_result = _run_git(repo_root, ["show", f"HEAD:{relative_path}"])
        if show_result.returncode != 0:
            return None

        head_text = show_result.stdout.decode("utf-8", errors="strict")
        head_normalized = normalize_text(head_text)

        if head_normalized != normalized_text:
            # Dirty working tree content differs from HEAD -> no valid
            # provenance for *this* ingested content.
            return None

        return head_sha
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError):
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_canonical_document(repo_root: Path, relative_path: str) -> LoadedDocument:
    """Load a single canonical document by its repository-relative path.

    Raises:
        AllowlistError: ``relative_path`` is not part of ``CANONICAL_DOCUMENTS``.
        CanonicalDocumentMissingError: the allowlisted file does not exist on disk.
    """
    spec = _DOCUMENTS_BY_RELATIVE_PATH.get(relative_path)
    if spec is None:
        allowed = ", ".join(sorted(_DOCUMENTS_BY_RELATIVE_PATH))
        raise AllowlistError(
            f"'{relative_path}' is not part of the canonical MVP allowlist. "
            f"Allowed paths: {allowed}"
        )

    absolute_path = repo_root / relative_path
    if not absolute_path.is_file():
        raise CanonicalDocumentMissingError(
            f"Canonical document '{relative_path}' not found at '{absolute_path}'."
        )

    raw_text = absolute_path.read_text(encoding="utf-8")
    normalized = normalize_text(raw_text)
    document_hash = compute_document_hash(normalized)
    source_git_commit = _resolve_source_git_commit(repo_root, relative_path, normalized)

    return LoadedDocument(
        document_id=spec.document_id,
        source_path=relative_path,
        canonical_domain=spec.canonical_domain,
        document_status=spec.document_status,
        text=normalized,
        document_hash=document_hash,
        source_git_commit=source_git_commit,
    )


def load_all_canonical_documents(repo_root: Path) -> list[LoadedDocument]:
    """Load exactly the five allowlisted documents, in deterministic order.

    Order follows ``CANONICAL_DOCUMENTS`` (declaration order), never
    filesystem iteration order.
    """
    return [
        load_canonical_document(repo_root, spec.relative_path)
        for spec in CANONICAL_DOCUMENTS
    ]
