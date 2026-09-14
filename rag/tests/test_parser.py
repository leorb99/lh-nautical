"""Unit tests for rag/src/ingestion/parser.py."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.ingestion.loader import (
    CANONICAL_DOCUMENTS,
    LoadedDocument,
    load_canonical_document,
)
from src.ingestion.parser import ParserError, parse_document

_STRICT_RULE_ID_RE = re.compile(r"^GOLD-\d{3}$")


def _doc(text: str) -> LoadedDocument:
    """Build a minimal LoadedDocument around raw (already-normalized) text."""
    return LoadedDocument(
        document_id="test_doc",
        source_path="rag/knowledge_base/test_doc.md",
        canonical_domain="business_rules",
        document_status="CLOSED",
        text=text,
        document_hash="0" * 64,
        source_git_commit=None,
    )


# ---------------------------------------------------------------------------
# H1 / document_title
# ---------------------------------------------------------------------------


def test_exactly_one_h1_sets_document_title():
    parsed = parse_document(_doc("# My Title\n\nIntro.\n"))
    assert parsed.document_title == "My Title"


def test_zero_h1_raises_parser_error():
    with pytest.raises(ParserError):
        parse_document(_doc("## Only H2\n\ntext\n"))


def test_two_h1_raises_parser_error():
    with pytest.raises(ParserError):
        parse_document(_doc("# First\n\n# Second\n"))


def test_h1_never_becomes_a_section():
    parsed = parse_document(_doc("# Title\n\n## Section A\n\nText A.\n"))
    assert all(s.section_title != "Title" for s in parsed.sections)
    assert all("# Title" not in s.text for s in parsed.sections)


def test_h1_title_strips_trailing_hashes():
    parsed = parse_document(_doc("# Title ##\n\ntext\n"))
    assert parsed.document_title == "Title"


def test_non_empty_content_before_h1_raises_parser_error():
    raw = "Texto indevido\n\n# Documento\n\n## Seção\n\ntext\n"
    with pytest.raises(ParserError):
        parse_document(_doc(raw))


def test_non_empty_content_before_h1_reports_first_invalid_line():
    raw = "\n\nTexto indevido\n\n# Documento\n\n## Seção\n\ntext\n"
    with pytest.raises(ParserError) as exc_info:
        parse_document(_doc(raw))
    message = str(exc_info.value)
    # Line 3 (1-indexed) is the first non-blank line before the H1; the
    # exact wording is not pinned down, only that the line number and the
    # source_path are surfaced somewhere in the error.
    assert "3" in message
    assert "rag/knowledge_base/test_doc.md" in message


def test_blank_lines_before_h1_are_still_allowed():
    parsed = parse_document(_doc("\n\n# Documento\n\n## Seção\n\ntext\n"))
    assert parsed.document_title == "Documento"


def test_no_pre_h1_content_section_is_ever_created():
    raw = "\n# Documento\n\n## Seção\n\ntext\n"
    parsed = parse_document(_doc(raw))
    # Only the "Seção" block should exist — nothing represents the blank
    # line(s) that preceded the H1.
    assert [s.section_title for s in parsed.sections] == ["Seção"]


# ---------------------------------------------------------------------------
# Introductory section (H1 -> first H2+)
# ---------------------------------------------------------------------------


def test_intro_section_captured_when_present():
    parsed = parse_document(
        _doc("# Doc\n\nIntroduction paragraph.\n\n## Section A\n\nText A.\n")
    )
    intro = parsed.sections[0]
    assert intro.heading_level is None
    assert intro.section_path is None
    assert intro.section_title is None
    assert intro.text == "Introduction paragraph."


def test_no_intro_section_when_only_blank_lines_before_first_heading():
    parsed = parse_document(_doc("# Doc\n\n\n\n## Section A\n\nText A.\n"))
    assert all(s.heading_level is not None for s in parsed.sections)


def test_no_intro_section_when_first_heading_immediately_follows_h1():
    parsed = parse_document(_doc("# Doc\n## Section A\n\nText A.\n"))
    assert all(s.heading_level is not None for s in parsed.sections)


def test_no_intro_section_when_document_has_no_h2_plus_headings():
    parsed = parse_document(_doc("# Doc\n\nOnly introduction, no other headings.\n"))
    assert len(parsed.sections) == 1
    assert parsed.sections[0].heading_level is None
    assert parsed.sections[0].text == "Only introduction, no other headings."


# ---------------------------------------------------------------------------
# section_path / section_title / heading hierarchy
# ---------------------------------------------------------------------------


def test_h2_h3_h4_hierarchy_builds_section_path():
    parsed = parse_document(
        _doc(
            "# Doc\n\n"
            "## Inventário\n\n"
            "### Reposição\n\n"
            "#### Lead time\n\ntext\n"
        )
    )
    by_title = {s.section_title: s for s in parsed.sections if s.section_title}

    assert by_title["Inventário"].section_path == "Inventário"
    assert by_title["Reposição"].section_path == "Inventário > Reposição"
    assert by_title["Lead time"].section_path == "Inventário > Reposição > Lead time"
    assert by_title["Lead time"].heading_level == 4


def test_heading_jump_does_not_synthesize_intermediate_levels():
    parsed = parse_document(
        _doc("# Doc\n\n## Inventário\n\n#### Lead time\n\ntext\n")
    )
    by_title = {s.section_title: s for s in parsed.sections if s.section_title}

    assert by_title["Lead time"].section_path == "Inventário > Lead time"
    assert by_title["Lead time"].heading_level == 4


def test_sibling_heading_replaces_deeper_ancestor():
    parsed = parse_document(
        _doc(
            "# Doc\n\n"
            "## A\n\ntext a\n\n"
            "### B\n\ntext b\n\n"
            "### C\n\ntext c\n"
        )
    )
    by_title = {s.section_title: s for s in parsed.sections if s.section_title}

    assert by_title["B"].section_path == "A > B"
    assert by_title["C"].section_path == "A > C"


def test_returning_to_shallower_level_drops_deeper_ancestors():
    parsed = parse_document(
        _doc(
            "# Doc\n\n"
            "## A\n\n"
            "### B\n\n"
            "## D\n\ntext d\n"
        )
    )
    by_title = {s.section_title: s for s in parsed.sections if s.section_title}
    assert by_title["D"].section_path == "D"


# ---------------------------------------------------------------------------
# Flat block boundaries — a section does not include its children's text
# ---------------------------------------------------------------------------


def test_parent_section_does_not_include_child_text():
    parsed = parse_document(
        _doc("# Doc\n\n## A\n\nTexto A.\n\n### B\n\nTexto B.\n")
    )
    by_title = {s.section_title: s for s in parsed.sections if s.section_title}

    assert by_title["A"].text == "## A\n\nTexto A."
    assert by_title["B"].text == "### B\n\nTexto B."
    assert "Texto B" not in by_title["A"].text


def test_heading_line_itself_is_kept_in_section_text():
    parsed = parse_document(_doc("# Doc\n\n## A\n\nTexto A.\n"))
    section_a = next(s for s in parsed.sections if s.section_title == "A")
    assert section_a.text.startswith("## A")


# ---------------------------------------------------------------------------
# Line numbers
# ---------------------------------------------------------------------------


def test_line_numbers_are_one_indexed_and_ordered():
    raw = "# Doc\n\nIntro.\n\n## A\n\nTexto A.\n"
    parsed = parse_document(_doc(raw))

    for section in parsed.sections:
        assert section.line_start <= section.line_end
        assert section.line_start >= 1

    lines = raw.split("\n")
    for section in parsed.sections:
        reconstructed = "\n".join(lines[section.line_start - 1 : section.line_end])
        assert reconstructed == section.text


def test_section_heading_line_is_line_start():
    parsed = parse_document(_doc("# Doc\n\nIntro.\n\n## A\n\nTexto A.\n"))
    section_a = next(s for s in parsed.sections if s.section_title == "A")
    # "## A" is line 5 (1-indexed) in the raw text above.
    assert section_a.line_start == 5


# ---------------------------------------------------------------------------
# Trimming of blank borders (internal blank lines preserved)
# ---------------------------------------------------------------------------


def test_trailing_blank_lines_trimmed_from_section_end():
    parsed = parse_document(_doc("# Doc\n\n## A\n\nTexto A.\n\n\n\n## B\n\nTexto B.\n"))
    section_a = next(s for s in parsed.sections if s.section_title == "A")
    assert not section_a.text.endswith("\n")
    assert section_a.text == "## A\n\nTexto A."


def test_internal_blank_lines_are_preserved():
    parsed = parse_document(
        _doc("# Doc\n\n## A\n\nPara 1.\n\n\nPara 2.\n")
    )
    section_a = next(s for s in parsed.sections if s.section_title == "A")
    assert section_a.text == "## A\n\nPara 1.\n\n\nPara 2."


# ---------------------------------------------------------------------------
# Fenced code blocks: headings inside are not structural
# ---------------------------------------------------------------------------


def test_heading_inside_fenced_code_block_is_ignored():
    raw = (
        "# Doc\n\n"
        "## Real Section\n\n"
        "```text\n"
        "## Isto não é uma seção\n"
        "```\n\n"
        "More text.\n"
    )
    parsed = parse_document(_doc(raw))
    titles = [s.section_title for s in parsed.sections if s.section_title]
    assert titles == ["Real Section"]


def test_fenced_block_content_preserved_in_section_text():
    raw = (
        "# Doc\n\n"
        "## Real Section\n\n"
        "```text\n"
        "## Isto não é uma seção\n"
        "```\n"
    )
    parsed = parse_document(_doc(raw))
    section = next(s for s in parsed.sections if s.section_title == "Real Section")
    assert "## Isto não é uma seção" in section.text
    assert "```" in section.text


def test_tilde_fence_also_masks_headings():
    raw = "# Doc\n\n## A\n\n~~~\n## not a heading\n~~~\n"
    parsed = parse_document(_doc(raw))
    titles = [s.section_title for s in parsed.sections if s.section_title]
    assert titles == ["A"]


# ---------------------------------------------------------------------------
# ATX heading recognition edge cases
# ---------------------------------------------------------------------------


def test_hash_without_following_space_is_not_a_heading():
    raw = "# Doc\n\n#NotAHeading\n\n## Real\n\ntext\n"
    parsed = parse_document(_doc(raw))
    titles = [s.section_title for s in parsed.sections if s.section_title]
    assert titles == ["Real"]


def test_seven_hashes_is_not_a_heading():
    raw = "# Doc\n\n####### Not a heading\n\n## Real\n\ntext\n"
    parsed = parse_document(_doc(raw))
    titles = [s.section_title for s in parsed.sections if s.section_title]
    assert titles == ["Real"]


def test_up_to_three_leading_spaces_still_a_heading():
    raw = "# Doc\n\n   ## Indented Heading\n\ntext\n"
    parsed = parse_document(_doc(raw))
    titles = [s.section_title for s in parsed.sections if s.section_title]
    assert titles == ["Indented Heading"]


def test_four_leading_spaces_is_not_a_heading():
    # 4+ leading spaces is an indented code block per CommonMark, not a heading.
    raw = "# Doc\n\n    ## Not A Heading\n\n## Real\n\ntext\n"
    parsed = parse_document(_doc(raw))
    titles = [s.section_title for s in parsed.sections if s.section_title]
    assert titles == ["Real"]


# ---------------------------------------------------------------------------
# Rule ID extraction
# ---------------------------------------------------------------------------


def test_rule_id_extracted_from_labelled_declaration_pattern():
    raw = (
        "# Doc\n\n"
        "## Regra\n\n"
        "Texto da regra.\n\n"
        "Rule ID:\n"
        "`GOLD-001`\n"
    )
    parsed = parse_document(_doc(raw))
    section = next(s for s in parsed.sections if s.section_title == "Regra")
    assert section.rule_ids == ("GOLD-001",)


def test_rule_id_extracted_from_bold_inline_pattern():
    raw = "# Doc\n\n## Metric\n\n**Rule ID:** `GOLD-004`\n"
    parsed = parse_document(_doc(raw))
    section = next(s for s in parsed.sections if s.section_title == "Metric")
    assert section.rule_ids == ("GOLD-004",)


def test_no_rule_id_yields_empty_tuple():
    parsed = parse_document(_doc("# Doc\n\n## A\n\nNo rule ids here.\n"))
    section = next(s for s in parsed.sections if s.section_title == "A")
    assert section.rule_ids == ()


def test_multiple_rule_ids_deduplicated_preserving_first_occurrence_order():
    raw = (
        "# Doc\n\n## A\n\n"
        "GOLD-020\n"
        "GOLD-005\n"
        "GOLD-020\n"
        "GOLD-011\n"
    )
    parsed = parse_document(_doc(raw))
    section = next(s for s in parsed.sections if s.section_title == "A")
    # First-occurrence order, not alphabetical/numerical order, and no
    # duplicate for the repeated GOLD-020.
    assert section.rule_ids == ("GOLD-020", "GOLD-005", "GOLD-011")


def test_rule_id_format_accepts_exactly_three_digits():
    for valid in ("GOLD-001", "GOLD-020", "GOLD-999"):
        raw = f"# Doc\n\n## A\n\n{valid}\n"
        parsed = parse_document(_doc(raw))
        section = next(s for s in parsed.sections if s.section_title == "A")
        assert section.rule_ids == (valid,), valid


def test_rule_id_format_rejects_wrong_digit_counts():
    for invalid in ("GOLD-1", "GOLD-01", "GOLD-0001", "GOLD-1234"):
        raw = f"# Doc\n\n## A\n\n{invalid}\n"
        parsed = parse_document(_doc(raw))
        section = next(s for s in parsed.sections if s.section_title == "A")
        assert section.rule_ids == (), invalid


def test_rule_id_format_does_not_partially_match_four_digit_id():
    # GOLD-0001 must not be captured as GOLD-000 (or any other 3-digit slice).
    raw = "# Doc\n\n## A\n\nGOLD-0001\n"
    parsed = parse_document(_doc(raw))
    section = next(s for s in parsed.sections if s.section_title == "A")
    assert "GOLD-000" not in section.rule_ids
    assert section.rule_ids == ()


def test_rule_id_in_one_section_does_not_leak_into_sibling_section():
    raw = "# Doc\n\n## A\n\nRule ID:\n`GOLD-001`\n\n## B\n\nNo rule id.\n"
    parsed = parse_document(_doc(raw))
    section_a = next(s for s in parsed.sections if s.section_title == "A")
    section_b = next(s for s in parsed.sections if s.section_title == "B")
    assert section_a.rule_ids == ("GOLD-001",)
    assert section_b.rule_ids == ()


# ---------------------------------------------------------------------------
# End-to-end validation against the five real canonical documents
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_parses_real_gold_business_rules_document():
    document = load_canonical_document(_REPO_ROOT, "rag/knowledge_base/gold_business_rules.md")
    parsed = parse_document(document)

    assert parsed.document_title == "Regras de Negócio da Camada Gold"
    assert len(parsed.sections) > 5

    all_rule_ids = {rid for s in parsed.sections for rid in s.rule_ids}
    assert "GOLD-001" in all_rule_ids
    assert "GOLD-007" in all_rule_ids

    section_titles = {s.section_title for s in parsed.sections if s.section_title}
    assert "Vendas efetivas" in section_titles


@pytest.mark.parametrize(
    "spec", CANONICAL_DOCUMENTS, ids=lambda spec: spec.document_id
)
def test_real_canonical_document_parses_with_valid_provenance(spec):
    """Persistent, per-document validation of the full loader -> parser
    pipeline against the five real canonical documents (not a synthetic
    fixture), covering: successful parsing, non-empty title/sections, valid
    line provenance, section_path never including the H1 title, and strict
    GOLD-NNN Rule ID formatting.
    """
    document = load_canonical_document(_REPO_ROOT, spec.relative_path)
    parsed = parse_document(document)

    assert parsed.document_title.strip() != ""
    assert len(parsed.sections) > 0

    source_lines = document.text.split("\n")

    for section in parsed.sections:
        assert section.text.strip() != ""
        assert section.line_start >= 1
        assert section.line_end >= section.line_start

        # Exact provenance reconstruction: line_start/line_end must point
        # precisely at the text persisted in the section after trimming.
        reconstructed = "\n".join(
            source_lines[section.line_start - 1 : section.line_end]
        )
        assert reconstructed == section.text

        if section.section_path is not None:
            first_component = section.section_path.split(" > ")[0]
            assert first_component != parsed.document_title

        for rule_id in section.rule_ids:
            assert _STRICT_RULE_ID_RE.match(rule_id), rule_id


def test_all_five_canonical_documents_are_present_and_distinct():
    assert len(CANONICAL_DOCUMENTS) == 5
    document_ids = {spec.document_id for spec in CANONICAL_DOCUMENTS}
    assert len(document_ids) == 5
    assert "powerbi_semantic_rules" not in document_ids
