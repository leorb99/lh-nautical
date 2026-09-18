"""Unit tests for src.evaluation.citations — deterministic citation
integrity validation (Phase 3B). Pure Python: no Databricks, no MLflow, no
LlamaIndex, no Harness. Source dictionaries are shaped exactly like real
OrchestrationResult.sources entries."""

from __future__ import annotations

import copy

from src.evaluation.citations import CitationIntegrityStatus, check_citation_integrity


def _source(label, **overrides):
    defaults = dict(
        source_label=label,
        chunk_id=f"chunk-{label}",
        document_id="doc-1",
        document_title="Title",
        section_path="A > B",
        section_title="B",
        rule_ids=[],
        rank=1,
        score=0.9,
    )
    defaults.update(overrides)
    return defaults


_SOURCES = [_source("S1"), _source("S2"), _source("S3")]


# ---------------------------------------------------------------------------
# 1. Single valid citation
# ---------------------------------------------------------------------------


def test_single_valid_citation():
    result = check_citation_integrity("De acordo com [S1], a regra é X.", _SOURCES)
    assert result.status is CitationIntegrityStatus.VALID
    assert result.citations_present is True
    assert result.cited_labels == ("S1",)
    assert result.valid_labels == ("S1",)
    assert result.unknown_labels == ()
    assert result.is_valid is True


# ---------------------------------------------------------------------------
# 2. Multiple valid citations in one bracket
# ---------------------------------------------------------------------------


def test_multiple_valid_citations_in_one_bracket():
    result = check_citation_integrity("Conforme [S1, S2], a regra é X.", _SOURCES)
    assert result.status is CitationIntegrityStatus.VALID
    assert result.cited_labels == ("S1", "S2")
    assert result.valid_labels == ("S1", "S2")
    assert result.unknown_labels == ()
    assert result.is_valid is True


# ---------------------------------------------------------------------------
# 3. Whitespace variants
# ---------------------------------------------------------------------------


def test_whitespace_variants_are_all_supported():
    for text in ("[S1,S2]", "[S1,  S2]", "[S1 , S2]", "[ S1, S2 ]"):
        result = check_citation_integrity(f"Conforme {text}.", _SOURCES)
        assert result.cited_labels == ("S1", "S2"), f"failed for {text!r}"
        assert result.is_valid is True


# ---------------------------------------------------------------------------
# 4. Unknown citation label
# ---------------------------------------------------------------------------


def test_unknown_citation_label():
    result = check_citation_integrity("Vide [S9] para detalhes.", _SOURCES)
    assert result.status is CitationIntegrityStatus.INVALID
    assert result.citations_present is True
    assert result.cited_labels == ("S9",)
    assert result.valid_labels == ()
    assert result.unknown_labels == ("S9",)
    assert result.is_valid is False


# ---------------------------------------------------------------------------
# 5. Mixture of valid + unknown labels
# ---------------------------------------------------------------------------


def test_mixture_of_valid_and_unknown_labels():
    result = check_citation_integrity("Conforme [S1, S9] e depois [S2].", _SOURCES)
    assert result.status is CitationIntegrityStatus.INVALID
    assert result.cited_labels == ("S1", "S9", "S2")
    assert result.valid_labels == ("S1", "S2")
    assert result.unknown_labels == ("S9",)
    assert result.is_valid is False


# ---------------------------------------------------------------------------
# 6. No citations
# ---------------------------------------------------------------------------


def test_no_citations():
    result = check_citation_integrity("A regra não cita nenhuma fonte.", _SOURCES)
    assert result.status is CitationIntegrityStatus.NO_CITATIONS
    assert result.citations_present is False
    assert result.cited_labels == ()
    assert result.valid_labels == ()
    assert result.unknown_labels == ()
    # "No citations to validate" is distinct from "citations are grounded" —
    # is_valid is True here only because there is no integrity violation,
    # never because the answer was judged grounded (see module docstring).
    assert result.is_valid is True


# ---------------------------------------------------------------------------
# 7. None answer
# ---------------------------------------------------------------------------


def test_none_answer():
    result = check_citation_integrity(None, _SOURCES)
    assert result.status is CitationIntegrityStatus.NO_CITATIONS
    assert result.citations_present is False
    assert result.cited_labels == ()
    assert result.unknown_labels == ()
    assert result.is_valid is True


# ---------------------------------------------------------------------------
# 8. Empty answer
# ---------------------------------------------------------------------------


def test_empty_answer():
    result = check_citation_integrity("", _SOURCES)
    assert result.status is CitationIntegrityStatus.NO_CITATIONS
    assert result.citations_present is False
    assert result.cited_labels == ()


# ---------------------------------------------------------------------------
# 9. Repeated citation
# ---------------------------------------------------------------------------


def test_repeated_citation_is_deduplicated_not_reported_as_separate_failures():
    result = check_citation_integrity("Primeiro [S1] e depois [S1] de novo.", _SOURCES)
    assert result.cited_labels == ("S1",)
    assert result.valid_labels == ("S1",)
    assert result.status is CitationIntegrityStatus.VALID


def test_repeated_unknown_citation_is_deduplicated():
    result = check_citation_integrity("Primeiro [S9] e depois [S9] de novo.", _SOURCES)
    assert result.cited_labels == ("S9",)
    assert result.unknown_labels == ("S9",)
    assert result.status is CitationIntegrityStatus.INVALID


# ---------------------------------------------------------------------------
# 10. Unbracketed S1 text is not treated as a citation
# ---------------------------------------------------------------------------


def test_unbracketed_label_text_is_not_treated_as_citation():
    result = check_citation_integrity("O valor S1 é ótimo, sem colchetes.", _SOURCES)
    assert result.status is CitationIntegrityStatus.NO_CITATIONS
    assert result.cited_labels == ()


def test_mixed_bracketed_and_unbracketed_only_counts_bracketed():
    result = check_citation_integrity("S1 é mencionado, mas só [S2] é citado.", _SOURCES)
    assert result.cited_labels == ("S2",)


# ---------------------------------------------------------------------------
# 11. Ordering is deterministic
# ---------------------------------------------------------------------------


def test_ordering_is_first_occurrence_across_the_whole_answer():
    result = check_citation_integrity("Primeiro [S3], depois [S1], depois [S2].", _SOURCES)
    assert result.cited_labels == ("S3", "S1", "S2")


def test_valid_and_unknown_labels_preserve_first_occurrence_order_within_each():
    result = check_citation_integrity("[S9] depois [S1] depois [S8] depois [S2].", _SOURCES)
    assert result.cited_labels == ("S9", "S1", "S8", "S2")
    assert result.valid_labels == ("S1", "S2")
    assert result.unknown_labels == ("S9", "S8")


# ---------------------------------------------------------------------------
# 12. Input sources are not mutated
# ---------------------------------------------------------------------------


def test_sources_are_not_mutated():
    sources = [_source("S1"), _source("S2")]
    sources_before = copy.deepcopy(sources)

    check_citation_integrity("Conforme [S1, S9].", sources)

    assert sources == sources_before


def test_answer_string_is_not_mutated_and_result_is_independent_object():
    answer = "Conforme [S1]."
    result = check_citation_integrity(answer, _SOURCES)
    assert answer == "Conforme [S1]."
    assert result.cited_labels is not answer  # sanity: distinct objects
