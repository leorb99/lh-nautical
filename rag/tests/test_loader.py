"""Unit tests for rag/src/ingestion/loader.py."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from src.ingestion.loader import (
    CANONICAL_DOCUMENTS,
    AllowlistError,
    CanonicalDocumentMissingError,
    compute_document_hash,
    load_all_canonical_documents,
    load_canonical_document,
    normalize_text,
)


# ---------------------------------------------------------------------------
# normalize_text
# ---------------------------------------------------------------------------


def test_normalize_text_strips_utf8_bom():
    assert normalize_text("﻿# Title\n") == "# Title\n"


def test_normalize_text_converts_crlf_to_lf():
    assert normalize_text("line1\r\nline2\r\n") == "line1\nline2\n"


def test_normalize_text_converts_lone_cr_to_lf():
    assert normalize_text("line1\rline2\r") == "line1\nline2\n"


def test_normalize_text_normalizes_unicode_to_nfc():
    # 'e' + combining acute accent (NFD) -> 'é' (NFC).
    nfd = "café"
    assert normalize_text(nfd) == "café"


def test_normalize_text_preserves_internal_blank_lines_and_trailing_spaces():
    raw = "line1   \n\n\nline2\n"
    assert normalize_text(raw) == raw


def test_normalize_text_does_not_strip_document():
    raw = "\n\n# Title\n\ntext\n\n"
    normalized = normalize_text(raw)
    assert normalized.startswith("\n\n")
    assert normalized.endswith("\n\n")


# ---------------------------------------------------------------------------
# compute_document_hash
# ---------------------------------------------------------------------------


def test_compute_document_hash_is_sha256_hex_64_chars():
    digest = compute_document_hash("hello world")
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)


def test_compute_document_hash_is_deterministic():
    assert compute_document_hash("same text") == compute_document_hash("same text")


def test_compute_document_hash_detects_real_changes():
    assert compute_document_hash("text a") != compute_document_hash("text b")


def test_compute_document_hash_matches_known_vector():
    # sha256("") is a well-known constant.
    assert (
        compute_document_hash("")
        == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )


# ---------------------------------------------------------------------------
# Fixtures: a fake repository root with the canonical directory structure.
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    kb_dir = tmp_path / "rag" / "knowledge_base"
    kb_dir.mkdir(parents=True)
    for spec in CANONICAL_DOCUMENTS:
        filename = spec.relative_path.split("/")[-1]
        (kb_dir / filename).write_text(
            f"# {spec.document_id}\n\nSome content for {spec.document_id}.\n",
            encoding="utf-8",
        )
    return tmp_path


# ---------------------------------------------------------------------------
# load_canonical_document
# ---------------------------------------------------------------------------


def test_load_canonical_document_reads_expected_metadata(fake_repo: Path):
    spec = CANONICAL_DOCUMENTS[0]
    doc = load_canonical_document(fake_repo, spec.relative_path)

    assert doc.document_id == spec.document_id
    assert doc.canonical_domain == spec.canonical_domain
    assert doc.document_status == "CLOSED"
    assert doc.source_path == spec.relative_path
    assert doc.text.startswith(f"# {spec.document_id}")


def test_load_canonical_document_source_path_is_relative(fake_repo: Path):
    spec = CANONICAL_DOCUMENTS[0]
    doc = load_canonical_document(fake_repo, spec.relative_path)

    assert not doc.source_path.startswith("/")
    assert ":\\" not in doc.source_path
    assert doc.source_path == "rag/knowledge_base/gold_business_rules.md"


def test_load_canonical_document_computes_document_hash(fake_repo: Path):
    spec = CANONICAL_DOCUMENTS[0]
    doc = load_canonical_document(fake_repo, spec.relative_path)

    expected_text = normalize_text(
        (fake_repo / spec.relative_path).read_text(encoding="utf-8")
    )
    assert doc.document_hash == compute_document_hash(expected_text)


def test_load_canonical_document_rejects_non_allowlisted_path(fake_repo: Path):
    with pytest.raises(AllowlistError):
        load_canonical_document(fake_repo, "rag/knowledge_base/powerbi_semantic_rules.md")


def test_load_canonical_document_rejects_arbitrary_path(fake_repo: Path):
    with pytest.raises(AllowlistError):
        load_canonical_document(fake_repo, "rag/README.md")


def test_load_canonical_document_missing_file_raises(fake_repo: Path):
    spec = CANONICAL_DOCUMENTS[0]
    (fake_repo / spec.relative_path).unlink()

    with pytest.raises(CanonicalDocumentMissingError):
        load_canonical_document(fake_repo, spec.relative_path)


def test_load_canonical_document_untracked_outside_git_has_no_commit(fake_repo: Path):
    # fake_repo is a plain tmp_path, not a Git repository at all.
    spec = CANONICAL_DOCUMENTS[0]
    doc = load_canonical_document(fake_repo, spec.relative_path)
    assert doc.source_git_commit is None


# ---------------------------------------------------------------------------
# load_all_canonical_documents
# ---------------------------------------------------------------------------


def test_load_all_canonical_documents_returns_exactly_five(fake_repo: Path):
    docs = load_all_canonical_documents(fake_repo)
    assert len(docs) == 5


def test_load_all_canonical_documents_order_is_deterministic(fake_repo: Path):
    docs_first_run = load_all_canonical_documents(fake_repo)
    docs_second_run = load_all_canonical_documents(fake_repo)

    ids_first = [d.document_id for d in docs_first_run]
    ids_second = [d.document_id for d in docs_second_run]

    assert ids_first == ids_second
    assert ids_first == [spec.document_id for spec in CANONICAL_DOCUMENTS]


def test_load_all_canonical_documents_excludes_powerbi(fake_repo: Path):
    docs = load_all_canonical_documents(fake_repo)
    ids = {d.document_id for d in docs}
    assert "powerbi_semantic_rules" not in ids


def test_load_all_canonical_documents_missing_one_raises(fake_repo: Path):
    spec = CANONICAL_DOCUMENTS[2]
    (fake_repo / spec.relative_path).unlink()

    with pytest.raises(CanonicalDocumentMissingError):
        load_all_canonical_documents(fake_repo)


# ---------------------------------------------------------------------------
# source_git_commit: conservative Git provenance rule
# ---------------------------------------------------------------------------


def _init_git_repo(repo_root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo_root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=repo_root, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_root, check=True)


def test_source_git_commit_is_none_for_untracked_file_in_git_repo(fake_repo: Path):
    _init_git_repo(fake_repo)
    # Nothing committed: the canonical files exist on disk but are untracked.
    spec = CANONICAL_DOCUMENTS[0]
    doc = load_canonical_document(fake_repo, spec.relative_path)
    assert doc.source_git_commit is None


def test_source_git_commit_is_sha_when_tracked_and_identical_to_head(fake_repo: Path):
    _init_git_repo(fake_repo)
    subprocess.run(["git", "add", "-A"], cwd=fake_repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=fake_repo, check=True)

    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=fake_repo, capture_output=True, check=True
    ).stdout.decode("utf-8").strip()

    spec = CANONICAL_DOCUMENTS[0]
    doc = load_canonical_document(fake_repo, spec.relative_path)
    assert doc.source_git_commit == head_sha


def test_source_git_commit_is_none_when_working_tree_diverges_from_head(fake_repo: Path):
    _init_git_repo(fake_repo)
    subprocess.run(["git", "add", "-A"], cwd=fake_repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=fake_repo, check=True)

    spec = CANONICAL_DOCUMENTS[0]
    absolute_path = fake_repo / spec.relative_path
    absolute_path.write_text(
        absolute_path.read_text(encoding="utf-8") + "\nExtra uncommitted line.\n",
        encoding="utf-8",
    )

    doc = load_canonical_document(fake_repo, spec.relative_path)
    assert doc.source_git_commit is None


def test_source_git_commit_never_crashes_outside_git_repo(fake_repo: Path):
    # fake_repo has no .git directory at all.
    for spec in CANONICAL_DOCUMENTS:
        doc = load_canonical_document(fake_repo, spec.relative_path)
        assert doc.source_git_commit is None
