"""Unit tests for rag/src/ingestion/chunker.py."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.ingestion.chunker import (
    Chunk,
    ChunkingConfig,
    ChunkingError,
    DEFAULT_CHUNKING_CONFIG,
    chunk_document,
    chunk_documents,
    estimate_token_count,
)
from src.ingestion.loader import CANONICAL_DOCUMENTS, LoadedDocument, load_canonical_document
from src.ingestion.parser import ParsedDocument, ParsedSection, parse_document

_STRICT_RULE_ID_RE = re.compile(r"^GOLD-\d{3}$")
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _word_counter(text: str) -> int:
    """Simple, fully predictable token counter for tests that need exact
    control over section sizes, independent of the default heuristic.
    """
    return len(text.split())


def _loaded_document(
    *,
    document_id: str = "test_doc",
    source_path: str = "rag/knowledge_base/test_doc.md",
    canonical_domain: str = "business_rules",
    document_status: str = "CLOSED",
    text: str = "",
    document_hash: str = "d" * 64,
    source_git_commit: str | None = "c" * 40,
) -> LoadedDocument:
    return LoadedDocument(
        document_id=document_id,
        source_path=source_path,
        canonical_domain=canonical_domain,
        document_status=document_status,
        text=text,
        document_hash=document_hash,
        source_git_commit=source_git_commit,
    )


def _section(
    *,
    heading_level: int | None = 2,
    section_path: str | None = "A",
    section_title: str | None = "A",
    line_start: int = 1,
    line_end: int | None = None,
    text: str,
    rule_ids: tuple[str, ...] = (),
) -> ParsedSection:
    if line_end is None:
        line_end = line_start + text.count("\n")
    return ParsedSection(
        heading_level=heading_level,
        section_path=section_path,
        section_title=section_title,
        line_start=line_start,
        line_end=line_end,
        text=text,
        rule_ids=rule_ids,
    )


def _document(sections: tuple[ParsedSection, ...], **loaded_kwargs) -> ParsedDocument:
    source = _loaded_document(**loaded_kwargs)
    return ParsedDocument(source=source, document_title="Test Document", sections=sections)


def _paragraphs(n: int, words_per_paragraph: int, *, word: str = "word") -> str:
    """Build `n` blank-line-separated paragraphs, each with an exact word
    count, so that a word-based token_counter can predict sizes exactly.
    """
    paras = []
    counter = 0
    for _ in range(n):
        words = []
        for _ in range(words_per_paragraph):
            words.append(f"{word}{counter}")
            counter += 1
        paras.append(" ".join(words))
    return "\n\n".join(paras)


# ---------------------------------------------------------------------------
# 1-6: ChunkingConfig validation
# ---------------------------------------------------------------------------


def test_config_defaults():
    config = ChunkingConfig()
    assert config.target_tokens == 512
    assert config.max_tokens == 800
    assert config.overlap_tokens == 64


def test_config_target_must_be_positive():
    with pytest.raises(ChunkingError):
        ChunkingConfig(target_tokens=0)
    with pytest.raises(ChunkingError):
        ChunkingConfig(target_tokens=-1)


def test_config_max_must_be_positive():
    with pytest.raises(ChunkingError):
        ChunkingConfig(max_tokens=0)
    with pytest.raises(ChunkingError):
        ChunkingConfig(max_tokens=-1)


def test_config_target_must_not_exceed_max():
    with pytest.raises(ChunkingError):
        ChunkingConfig(target_tokens=900, max_tokens=800)


def test_config_overlap_must_be_non_negative():
    with pytest.raises(ChunkingError):
        ChunkingConfig(overlap_tokens=-1)


def test_config_overlap_must_be_less_than_target():
    with pytest.raises(ChunkingError):
        ChunkingConfig(target_tokens=100, overlap_tokens=100)
    with pytest.raises(ChunkingError):
        ChunkingConfig(target_tokens=100, overlap_tokens=150)


# ---------------------------------------------------------------------------
# 7-14: Section behavior
# ---------------------------------------------------------------------------


def test_small_section_produces_one_chunk():
    section = _section(text="## A\n\nSmall content.")
    document = _document((section,))
    chunks = chunk_document(document)
    assert len(chunks) == 1
    assert chunks[0].chunk_text == section.text


def test_section_below_target_stays_one_chunk():
    section = _section(text="## A\n\n" + " ".join(f"w{i}" for i in range(50)))
    document = _document((section,))
    config = ChunkingConfig(target_tokens=512, max_tokens=800, overlap_tokens=64)
    chunks = chunk_document(document, config=config)
    assert len(chunks) == 1


def test_section_above_target_below_max_stays_one_chunk():
    # 600 words > target(512) but < max(800): must NOT be split.
    text = "## A\n\n" + " ".join(f"w{i}" for i in range(600))
    section = _section(text=text)
    document = _document((section,))
    config = ChunkingConfig(target_tokens=512, max_tokens=800, overlap_tokens=64)
    chunks = chunk_document(document, config=config, token_counter=_word_counter)
    assert len(chunks) == 1
    assert chunks[0].chunk_text == text


def test_section_exactly_at_max_stays_one_chunk():
    # "##" and "A" are themselves 2 words under _word_counter, so 798 extra
    # words brings the total to exactly 800.
    text = "## A\n\n" + " ".join(f"w{i}" for i in range(798))
    section = _section(text=text)
    document = _document((section,))
    config = ChunkingConfig(target_tokens=512, max_tokens=800, overlap_tokens=64)
    assert _word_counter(text) == 800
    chunks = chunk_document(document, config=config, token_counter=_word_counter)
    assert len(chunks) == 1


def test_section_above_max_is_split():
    text = "## A\n\n" + _paragraphs(9, 100)  # 9 paragraphs * 100 words = 900 words
    section = _section(text=text)
    document = _document((section,))
    config = ChunkingConfig(target_tokens=512, max_tokens=800, overlap_tokens=64)
    assert _word_counter(text) > 800
    chunks = chunk_document(document, config=config, token_counter=_word_counter)
    assert len(chunks) > 1


def test_no_chunk_is_ever_empty():
    text = "## A\n\n" + _paragraphs(9, 100)
    section = _section(text=text)
    document = _document((section,))
    chunks = chunk_document(document, token_counter=_word_counter)
    assert all(c.chunk_text.strip() != "" for c in chunks)


def test_textual_order_is_preserved_across_split_chunks():
    text = "## A\n\n" + _paragraphs(9, 100)
    section = _section(text=text)
    document = _document((section,))
    chunks = chunk_document(document, token_counter=_word_counter)

    # The first word of each successive chunk's "new" content should never
    # regress to an earlier position than the previous chunk's start.
    assert [c.line_start for c in chunks] == sorted(c.line_start for c in chunks)
    assert [c.line_end for c in chunks] == sorted(c.line_end for c in chunks)


def test_chunks_never_mix_two_sections():
    section_a = _section(section_path="A", section_title="A", line_start=1, text="## A\n\nContent A.")
    section_b = _section(
        section_path="B",
        section_title="B",
        line_start=section_a.line_end + 2,
        text="## B\n\nContent B.",
    )
    document = _document((section_a, section_b))
    chunks = chunk_document(document)
    assert len(chunks) == 2
    assert "Content B" not in chunks[0].chunk_text
    assert "Content A" not in chunks[1].chunk_text
    assert chunks[0].section_title == "A"
    assert chunks[1].section_title == "B"


# ---------------------------------------------------------------------------
# 15-19: Overlap
# ---------------------------------------------------------------------------


def test_different_sections_have_no_overlap():
    section_a = _section(section_path="A", section_title="A", line_start=1, text="## A\n\nContent A only.")
    section_b = _section(
        section_path="B",
        section_title="B",
        line_start=section_a.line_end + 2,
        text="## B\n\nContent B only.",
    )
    document = _document((section_a, section_b))
    chunks = chunk_document(document)
    assert chunks[0].line_end < chunks[1].line_start


def test_subchunks_of_same_section_may_overlap():
    # Paragraphs of 35 words each comfortably fit within overlap_tokens=64
    # (unlike the 100-word paragraphs used to test the zero-overlap case),
    # so at least one whole trailing paragraph should be pulled in as overlap.
    text = "## A\n\n" + _paragraphs(25, 35)
    section = _section(text=text)
    document = _document((section,))
    config = ChunkingConfig(target_tokens=512, max_tokens=800, overlap_tokens=64)
    chunks = chunk_document(document, config=config, token_counter=_word_counter)
    assert len(chunks) > 1
    # At least one pair of consecutive chunks from the same section overlaps.
    overlaps = [
        chunks[i].line_start <= chunks[i - 1].line_end for i in range(1, len(chunks))
    ]
    assert any(overlaps)


def test_overlap_never_exceeds_max_tokens():
    text = "## A\n\n" + _paragraphs(9, 100)
    section = _section(text=text)
    document = _document((section,))
    config = ChunkingConfig(target_tokens=512, max_tokens=800, overlap_tokens=64)
    chunks = chunk_document(document, config=config, token_counter=_word_counter)
    for chunk in chunks:
        assert _word_counter(chunk.chunk_text) <= config.max_tokens


def test_overlap_content_is_a_prefix_of_the_next_chunk_in_source_order():
    text = "## A\n\n" + _paragraphs(25, 35)
    section = _section(text=text)
    document = _document((section,))
    config = ChunkingConfig(target_tokens=512, max_tokens=800, overlap_tokens=64)
    chunks = chunk_document(document, config=config, token_counter=_word_counter)

    at_least_one_overlap_checked = False
    for i in range(1, len(chunks)):
        prev_chunk, curr_chunk = chunks[i - 1], chunks[i]
        if curr_chunk.line_start <= prev_chunk.line_end:
            # Overlapping region: the overlapping words must appear in the
            # same order in both chunks (no shuffling / reversal).
            overlap_word = curr_chunk.chunk_text.split()[0]
            assert overlap_word in prev_chunk.chunk_text
            at_least_one_overlap_checked = True
    assert at_least_one_overlap_checked


def test_overlap_can_be_zero_when_no_block_fits_budget():
    text = "## A\n\n" + _paragraphs(9, 100)
    section = _section(text=text)
    document = _document((section,))
    # overlap_tokens=1 is smaller than any single 100-word paragraph, so no
    # whole atomic block can ever fit the overlap budget.
    config = ChunkingConfig(target_tokens=512, max_tokens=800, overlap_tokens=1)
    chunks = chunk_document(document, config=config, token_counter=_word_counter)
    assert len(chunks) > 1
    for i in range(1, len(chunks)):
        assert chunks[i].line_start > chunks[i - 1].line_end


# ---------------------------------------------------------------------------
# 20-28: Metadata propagation
# ---------------------------------------------------------------------------


def test_chunk_metadata_is_propagated_correctly():
    section = _section(section_path="Vendas > A", section_title="A", text="## A\n\nSome content.")
    document = _document(
        (section,),
        document_id="gold_business_rules",
        source_path="rag/knowledge_base/gold_business_rules.md",
        canonical_domain="business_rules",
        document_status="CLOSED",
        document_hash="e" * 64,
        source_git_commit="f" * 40,
    )
    chunk = chunk_document(document)[0]

    assert chunk.document_id == "gold_business_rules"
    assert chunk.document_title == "Test Document"
    assert chunk.source_path == "rag/knowledge_base/gold_business_rules.md"
    assert chunk.canonical_domain == "business_rules"
    assert chunk.document_status == "CLOSED"
    assert chunk.section_path == "Vendas > A"
    assert chunk.section_title == "A"
    assert chunk.document_hash == "e" * 64
    assert chunk.source_git_commit == "f" * 40


def test_chunk_metadata_propagates_none_source_git_commit():
    section = _section(text="## A\n\nSome content.")
    document = _document((section,), source_git_commit=None)
    chunk = chunk_document(document)[0]
    assert chunk.source_git_commit is None


def test_chunk_does_not_include_ingested_at_field():
    assert not hasattr(Chunk, "ingested_at")
    field_names = {f for f in Chunk.__dataclass_fields__}
    assert "ingested_at" not in field_names


def test_chunk_does_not_include_embedding_field():
    field_names = {f for f in Chunk.__dataclass_fields__}
    assert not any("embedding" in f or "vector" in f for f in field_names)


# ---------------------------------------------------------------------------
# 29-32: chunk_index
# ---------------------------------------------------------------------------


def test_chunk_index_starts_at_zero():
    section = _section(text="## A\n\nSmall content.")
    document = _document((section,))
    chunks = chunk_document(document)
    assert chunks[0].chunk_index == 0


def test_chunk_index_is_global_across_sections_not_reset_per_section():
    section_a = _section(section_path="A", section_title="A", line_start=1, text="## A\n\nContent A.")
    section_b_text = "## B\n\n" + _paragraphs(9, 100)
    section_b = _section(
        section_path="B",
        section_title="B",
        line_start=section_a.line_end + 2,
        text=section_b_text,
    )
    section_c = _section(
        section_path="C",
        section_title="C",
        line_start=section_b.line_end + 2,
        text="## C\n\nContent C.",
    )
    document = _document((section_a, section_b, section_c))
    config = ChunkingConfig(target_tokens=512, max_tokens=800, overlap_tokens=64)
    chunks = chunk_document(document, config=config, token_counter=_word_counter)

    assert len(chunks) > 3  # section B was split into more than one chunk
    indexes = [c.chunk_index for c in chunks]
    assert indexes == list(range(len(chunks)))
    # The last section's chunk does not restart at 0.
    assert chunks[-1].chunk_index == len(chunks) - 1


def test_chunk_indexes_are_contiguous_with_no_gaps():
    text = "## A\n\n" + _paragraphs(9, 100)
    section = _section(text=text)
    document = _document((section,))
    chunks = chunk_document(document, token_counter=_word_counter)
    indexes = [c.chunk_index for c in chunks]
    assert indexes == list(range(len(chunks)))


# ---------------------------------------------------------------------------
# 33-39: Rule IDs
# ---------------------------------------------------------------------------


def test_unsplit_section_rule_ids_match_actual_chunk_text():
    text = "## A\n\nSee GOLD-020 for details."
    # Deliberately wrong section.rule_ids to prove the chunker recomputes
    # from chunk_text rather than trusting ParsedSection.rule_ids.
    section = _section(text=text, rule_ids=("GOLD-999",))
    document = _document((section,))
    chunk = chunk_document(document)[0]
    assert chunk.rule_ids == ("GOLD-020",)


def test_split_subchunks_recompute_rule_ids_independently():
    para_with_id_a = "GOLD-020 " + " ".join(f"w{i}" for i in range(99))
    para_with_id_b = "GOLD-021 " + " ".join(f"w{i}" for i in range(99))
    filler = _paragraphs(7, 100, word="filler")
    text = "## A\n\n" + para_with_id_a + "\n\n" + filler + "\n\n" + para_with_id_b
    section = _section(text=text)
    document = _document((section,))
    config = ChunkingConfig(target_tokens=512, max_tokens=800, overlap_tokens=64)
    chunks = chunk_document(document, config=config, token_counter=_word_counter)

    assert len(chunks) > 1
    # GOLD-020 only appears in chunks whose text actually contains it.
    for chunk in chunks:
        if "GOLD-020" in chunk.chunk_text:
            assert "GOLD-020" in chunk.rule_ids
        else:
            assert "GOLD-020" not in chunk.rule_ids
        if "GOLD-021" in chunk.chunk_text:
            assert "GOLD-021" in chunk.rule_ids
        else:
            assert "GOLD-021" not in chunk.rule_ids


def test_rule_id_duplicates_removed_preserving_first_occurrence_order():
    text = "## A\n\nGOLD-020 then GOLD-005 then GOLD-020 again then GOLD-011."
    section = _section(text=text)
    document = _document((section,))
    chunk = chunk_document(document)[0]
    assert chunk.rule_ids == ("GOLD-020", "GOLD-005", "GOLD-011")


def test_rule_id_strict_format_rejects_wrong_digit_count():
    text = "## A\n\nGOLD-0001 is not a valid Rule ID."
    section = _section(text=text)
    document = _document((section,))
    chunk = chunk_document(document)[0]
    assert chunk.rule_ids == ()


def test_rule_id_empty_when_none_present():
    section = _section(text="## A\n\nNo rule ids here at all.")
    document = _document((section,))
    chunk = chunk_document(document)[0]
    assert chunk.rule_ids == ()


def test_all_extracted_rule_ids_match_strict_pattern():
    text = "## A\n\nGOLD-001 GOLD-999 GOLD-042"
    section = _section(text=text)
    document = _document((section,))
    chunk = chunk_document(document)[0]
    assert all(_STRICT_RULE_ID_RE.match(rid) for rid in chunk.rule_ids)


# ---------------------------------------------------------------------------
# 40-48: Hashing
# ---------------------------------------------------------------------------


def test_content_hash_is_sha256_hex_64():
    section = _section(text="## A\n\nSome content.")
    document = _document((section,))
    chunk = chunk_document(document)[0]
    assert len(chunk.content_hash) == 64
    assert all(c in "0123456789abcdef" for c in chunk.content_hash)


def test_same_chunk_text_same_content_hash():
    section1 = _section(text="## A\n\nIdentical content.")
    section2 = _section(text="## A\n\nIdentical content.")
    doc1 = _document((section1,))
    doc2 = _document((section2,))
    chunk1 = chunk_document(doc1)[0]
    chunk2 = chunk_document(doc2)[0]
    assert chunk1.content_hash == chunk2.content_hash


def test_different_chunk_text_different_content_hash():
    section1 = _section(text="## A\n\nContent one.")
    section2 = _section(text="## A\n\nContent two.")
    chunk1 = chunk_document(_document((section1,)))[0]
    chunk2 = chunk_document(_document((section2,)))[0]
    assert chunk1.content_hash != chunk2.content_hash


def test_chunk_id_is_sha256_hex_64():
    section = _section(text="## A\n\nSome content.")
    document = _document((section,))
    chunk = chunk_document(document)[0]
    assert len(chunk.chunk_id) == 64
    assert all(c in "0123456789abcdef" for c in chunk.chunk_id)


def test_same_inputs_produce_same_chunk_id():
    section = _section(text="## A\n\nSame content twice.")
    document = _document((section,))
    chunk1 = chunk_document(document)[0]
    chunk2 = chunk_document(document)[0]
    assert chunk1.chunk_id == chunk2.chunk_id


def test_different_content_hash_changes_chunk_id():
    section1 = _section(text="## A\n\nContent one.")
    section2 = _section(text="## A\n\nContent two.")
    chunk1 = chunk_document(_document((section1,)))[0]
    chunk2 = chunk_document(_document((section2,)))[0]
    assert chunk1.content_hash != chunk2.content_hash
    assert chunk1.chunk_id != chunk2.chunk_id


def test_different_chunk_index_changes_chunk_id():
    section_a = _section(section_path="A", section_title="A", line_start=1, text="## A\n\nSame text.")
    section_b = _section(
        section_path="A",
        section_title="A",
        line_start=section_a.line_end + 2,
        text="## A\n\nSame text.",
    )
    document = _document((section_a, section_b))
    chunks = chunk_document(document)
    assert chunks[0].content_hash == chunks[1].content_hash  # same text
    assert chunks[0].chunk_index != chunks[1].chunk_index
    assert chunks[0].chunk_id != chunks[1].chunk_id


def test_different_section_path_changes_chunk_id():
    section_a = _section(section_path="A", section_title="A", text="## A\n\nSame text.")
    section_b = _section(section_path="B", section_title="B", text="## A\n\nSame text.")
    chunk_a = chunk_document(_document((section_a,)))[0]
    chunk_b = chunk_document(_document((section_b,)))[0]
    assert chunk_a.content_hash == chunk_b.content_hash
    assert chunk_a.section_path != chunk_b.section_path
    assert chunk_a.chunk_id != chunk_b.chunk_id


def test_chunk_id_is_not_a_random_uuid():
    section = _section(text="## A\n\nDeterminism check.")
    document = _document((section,))
    ids = {chunk_document(document)[0].chunk_id for _ in range(5)}
    assert len(ids) == 1  # never random across repeated calls


# ---------------------------------------------------------------------------
# 49-54: Provenance
# ---------------------------------------------------------------------------


def test_chunk_line_start_within_section_bounds():
    text = "## A\n\n" + _paragraphs(9, 100)
    section = _section(text=text)
    document = _document((section,))
    chunks = chunk_document(document, token_counter=_word_counter)
    for chunk in chunks:
        assert chunk.line_start >= section.line_start


def test_chunk_line_end_within_section_bounds():
    text = "## A\n\n" + _paragraphs(9, 100)
    section = _section(text=text)
    document = _document((section,))
    chunks = chunk_document(document, token_counter=_word_counter)
    for chunk in chunks:
        assert chunk.line_end <= section.line_end


def test_chunk_line_start_le_line_end():
    text = "## A\n\n" + _paragraphs(9, 100)
    section = _section(text=text)
    document = _document((section,))
    chunks = chunk_document(document, token_counter=_word_counter)
    for chunk in chunks:
        assert chunk.line_start <= chunk.line_end


def test_unsplit_section_preserves_exact_parser_line_range():
    section = _section(line_start=10, text="## A\n\nSmall content.")
    document = _document((section,))
    chunk = chunk_document(document)[0]
    assert chunk.line_start == section.line_start
    assert chunk.line_end == section.line_end


def test_natural_chunk_matches_source_text_slice():
    text = "## A\n\n" + _paragraphs(9, 100)
    section = _section(line_start=1, text=text)
    document = _document((section,))
    config = ChunkingConfig(target_tokens=512, max_tokens=800, overlap_tokens=0)
    chunks = chunk_document(document, config=config, token_counter=_word_counter)

    source_lines = text.split("\n")
    for chunk in chunks:
        local_start = chunk.line_start - section.line_start
        local_end = chunk.line_end - section.line_start + 1
        expected = "\n".join(source_lines[local_start:local_end])
        assert chunk.chunk_text == expected


def test_overlap_ranges_only_overlap_within_same_section():
    section_a = _section(
        section_path="A", section_title="A", line_start=1, text="## A\n\n" + _paragraphs(9, 100)
    )
    section_b = _section(
        section_path="B",
        section_title="B",
        line_start=section_a.line_end + 2,
        text="## B\n\nSmall content.",
    )
    document = _document((section_a, section_b))
    config = ChunkingConfig(target_tokens=512, max_tokens=800, overlap_tokens=64)
    chunks = chunk_document(document, config=config, token_counter=_word_counter)

    section_a_chunks = [c for c in chunks if c.section_title == "A"]
    section_b_chunk = next(c for c in chunks if c.section_title == "B")
    assert section_a_chunks[-1].line_end < section_b_chunk.line_start


# ---------------------------------------------------------------------------
# 55-58: Fenced code blocks
# ---------------------------------------------------------------------------


def test_fenced_block_that_fits_is_not_broken():
    fenced = "```text\n" + "\n".join(f"line{i}" for i in range(5)) + "\n```"
    text = "## A\n\n" + fenced + "\n\n" + _paragraphs(8, 100)
    section = _section(text=text)
    document = _document((section,))
    config = ChunkingConfig(target_tokens=512, max_tokens=800, overlap_tokens=0)
    chunks = chunk_document(document, config=config, token_counter=_word_counter)

    # The fenced block must appear intact (open+close together) in exactly
    # one chunk, never split across two.
    matching = [c for c in chunks if "```text" in c.chunk_text]
    assert len(matching) == 1
    assert matching[0].chunk_text.count("```") == 2


def test_heading_like_text_inside_fenced_block_does_not_interfere():
    fenced = "```text\n## Isto não é uma seção\n```"
    text = "## A\n\n" + fenced + "\n\nMore content after."
    section = _section(text=text)
    document = _document((section,))
    chunk = chunk_document(document)[0]
    assert "## Isto não é uma seção" in chunk.chunk_text
    assert chunk.section_title == "A"  # unaffected by the fenced heading-like text


def test_fenced_block_content_remains_textual_and_ordered():
    fenced = "```text\nfirst\nsecond\nthird\n```"
    text = "## A\n\n" + fenced
    section = _section(text=text)
    document = _document((section,))
    chunk = chunk_document(document)[0]
    assert chunk.chunk_text.index("first") < chunk.chunk_text.index("second") < chunk.chunk_text.index("third")


def test_oversized_fenced_block_falls_back_to_line_splitting():
    # Each fenced line is small individually, but the whole fenced block
    # exceeds max_tokens as a single atomic unit -> line-splitting fallback.
    fenced_lines = [f"code_line_{i}" for i in range(50)]
    fenced = "```text\n" + "\n".join(fenced_lines) + "\n```"
    text = "## A\n\n" + fenced
    section = _section(text=text)
    document = _document((section,))
    config = ChunkingConfig(target_tokens=10, max_tokens=20, overlap_tokens=0)

    # Must not raise: falls back to per-line packing rather than refusing.
    chunks = chunk_document(document, config=config, token_counter=_word_counter)
    assert len(chunks) > 1
    all_text = "".join(c.chunk_text for c in chunks)
    for line in fenced_lines:
        assert line in all_text


# ---------------------------------------------------------------------------
# Real canonical documents: loader -> parser -> chunker
# ---------------------------------------------------------------------------


def _chunk_real_document(spec) -> tuple[ParsedDocument, tuple[Chunk, ...]]:
    loaded = load_canonical_document(_REPO_ROOT, spec.relative_path)
    parsed = parse_document(loaded)
    chunks = chunk_document(parsed)
    return parsed, chunks


@pytest.mark.parametrize("spec", CANONICAL_DOCUMENTS, ids=lambda spec: spec.document_id)
def test_real_canonical_document_chunks_are_well_formed(spec):
    _, chunks = _chunk_real_document(spec)

    assert len(chunks) > 0
    assert all(c.chunk_text.strip() != "" for c in chunks)

    indexes = [c.chunk_index for c in chunks]
    assert indexes == list(range(len(chunks)))

    chunk_ids = [c.chunk_id for c in chunks]
    assert len(chunk_ids) == len(set(chunk_ids))  # no duplicate chunk_id

    for chunk in chunks:
        assert len(chunk.content_hash) == 64
        assert len(chunk.chunk_id) == 64
        assert chunk.document_id == spec.document_id
        assert chunk.source_path == spec.relative_path
        assert chunk.canonical_domain == spec.canonical_domain
        assert chunk.document_status == "CLOSED"
        assert chunk.line_start >= 1
        assert chunk.line_end >= chunk.line_start
        for rule_id in chunk.rule_ids:
            assert _STRICT_RULE_ID_RE.match(rule_id)


@pytest.mark.parametrize("spec", CANONICAL_DOCUMENTS, ids=lambda spec: spec.document_id)
def test_real_canonical_document_chunking_is_deterministic(spec):
    _, chunks_run_1 = _chunk_real_document(spec)
    _, chunks_run_2 = _chunk_real_document(spec)

    ids_1 = [(c.chunk_index, c.chunk_id, c.content_hash) for c in chunks_run_1]
    ids_2 = [(c.chunk_index, c.chunk_id, c.content_hash) for c in chunks_run_2]
    assert ids_1 == ids_2


def test_real_documents_never_mix_source_path_or_document_id():
    documents = [load_canonical_document(_REPO_ROOT, spec.relative_path) for spec in CANONICAL_DOCUMENTS]
    parsed_documents = [parse_document(d) for d in documents]
    all_chunks = chunk_documents(parsed_documents)

    by_document_id = {}
    for chunk in all_chunks:
        by_document_id.setdefault(chunk.document_id, set()).add(chunk.source_path)

    for document_id, source_paths in by_document_id.items():
        assert len(source_paths) == 1, f"{document_id} mixed source_path values: {source_paths}"


def test_real_documents_chunk_index_resets_per_document():
    documents = [load_canonical_document(_REPO_ROOT, spec.relative_path) for spec in CANONICAL_DOCUMENTS]
    parsed_documents = [parse_document(d) for d in documents]
    all_chunks = chunk_documents(parsed_documents)

    by_document_id: dict[str, list[int]] = {}
    for chunk in all_chunks:
        by_document_id.setdefault(chunk.document_id, []).append(chunk.chunk_index)

    for document_id, indexes in by_document_id.items():
        assert indexes == list(range(len(indexes))), document_id


def test_real_documents_no_chunk_exceeds_hard_max_with_default_config():
    """Regression guard: with the current canonical documents and the
    default token estimator, no chunk should exceed max_tokens. If this
    ever fails after a knowledge-base edit, it signals a section/atomic
    block that genuinely needs the oversized-block fallback path and must
    be reviewed explicitly (see the Chunker Implementation Report).
    """
    for spec in CANONICAL_DOCUMENTS:
        _, chunks = _chunk_real_document(spec)
        for chunk in chunks:
            assert estimate_token_count(chunk.chunk_text) <= DEFAULT_CHUNKING_CONFIG.max_tokens, (
                spec.document_id,
                chunk.section_path,
            )


# ---------------------------------------------------------------------------
# Heading-only sections: no retrieval unit for a heading with no content
# of its own (structural check, never a token-size heuristic).
# ---------------------------------------------------------------------------


def test_h2_containing_only_heading_produces_zero_chunks():
    section = _section(heading_level=2, section_path="Vendas", section_title="Vendas", text="## Vendas")
    document = _document((section,))
    chunks = chunk_document(document)
    assert chunks == ()


def test_h3_containing_only_heading_produces_zero_chunks():
    section = _section(
        heading_level=3, section_path="A > B", section_title="B", text="### B"
    )
    document = _document((section,))
    chunks = chunk_document(document)
    assert chunks == ()


def test_heading_followed_by_blank_lines_produces_zero_chunks():
    section = _section(heading_level=2, text="## Vendas\n\n\n")
    document = _document((section,))
    assert chunk_document(document) == ()


def test_heading_followed_by_whitespace_only_lines_produces_zero_chunks():
    section = _section(heading_level=2, text="## Vendas\n   \n\t\n")
    document = _document((section,))
    assert chunk_document(document) == ()


def test_heading_with_real_short_text_generates_chunk():
    section = _section(heading_level=2, text="## Objetivo\n\nEste documento descreve...")
    document = _document((section,))
    chunks = chunk_document(document)
    assert len(chunks) == 1
    assert chunks[0].chunk_text == section.text


def test_heading_with_rule_id_generates_chunk():
    section = _section(heading_level=2, text="## A\n\nGOLD-020")
    document = _document((section,))
    chunks = chunk_document(document)
    assert len(chunks) == 1
    assert chunks[0].rule_ids == ("GOLD-020",)


def test_short_introduction_without_heading_level_generates_chunk():
    intro = _section(heading_level=None, section_path=None, section_title=None, text="Intro curta.")
    document = _document((intro,))
    chunks = chunk_document(document)
    assert len(chunks) == 1
    assert chunks[0].chunk_text == "Intro curta."


def test_heading_only_parent_skipped_child_with_content_kept():
    parent = _section(
        heading_level=2, section_path="Vendas", section_title="Vendas", line_start=1, text="## Vendas"
    )
    child = _section(
        heading_level=3,
        section_path="Vendas > Vendas efetivas",
        section_title="Vendas efetivas",
        line_start=parent.line_end + 1,
        text="### Vendas efetivas\n\nRegra...",
    )
    document = _document((parent, child))
    chunks = chunk_document(document)
    assert len(chunks) == 1
    assert chunks[0].section_title == "Vendas efetivas"


def test_child_of_heading_only_parent_keeps_full_section_path():
    parent = _section(
        heading_level=2, section_path="Vendas", section_title="Vendas", line_start=1, text="## Vendas"
    )
    child = _section(
        heading_level=3,
        section_path="Vendas > Vendas efetivas",
        section_title="Vendas efetivas",
        line_start=parent.line_end + 1,
        text="### Vendas efetivas\n\nRegra...",
    )
    document = _document((parent, child))
    chunk = chunk_document(document)[0]
    assert chunk.section_path == "Vendas > Vendas efetivas"


def test_multiple_heading_only_sections_do_not_create_index_gaps():
    heading_only_1 = _section(
        heading_level=2, section_path="A", section_title="A", line_start=1, text="## A"
    )
    real_1 = _section(
        heading_level=3,
        section_path="A > A1",
        section_title="A1",
        line_start=heading_only_1.line_end + 1,
        text="### A1\n\nContent 1.",
    )
    heading_only_2 = _section(
        heading_level=2,
        section_path="B",
        section_title="B",
        line_start=real_1.line_end + 1,
        text="## B",
    )
    real_2 = _section(
        heading_level=3,
        section_path="B > B1",
        section_title="B1",
        line_start=heading_only_2.line_end + 1,
        text="### B1\n\nContent 2.",
    )
    document = _document((heading_only_1, real_1, heading_only_2, real_2))
    chunks = chunk_document(document)
    assert len(chunks) == 2
    assert [c.chunk_index for c in chunks] == [0, 1]


def test_first_heading_only_section_still_yields_chunk_index_zero_for_first_real_chunk():
    heading_only = _section(
        heading_level=2, section_path="A", section_title="A", line_start=1, text="## A"
    )
    real = _section(
        heading_level=3,
        section_path="A > A1",
        section_title="A1",
        line_start=heading_only.line_end + 1,
        text="### A1\n\nContent.",
    )
    document = _document((heading_only, real))
    chunks = chunk_document(document)
    assert chunks[0].chunk_index == 0


def test_document_of_only_heading_only_sections_returns_empty_tuple_without_crash():
    section_a = _section(heading_level=2, section_path="A", section_title="A", line_start=1, text="## A")
    section_b = _section(
        heading_level=2,
        section_path="B",
        section_title="B",
        line_start=section_a.line_end + 1,
        text="## B",
    )
    document = _document((section_a, section_b))
    chunks = chunk_document(document)
    assert chunks == ()


# ---------------------------------------------------------------------------
# Oversized indivisible lexical segment: explicit ChunkingError rather than
# a silently oversized chunk or invented sub-line provenance.
# ---------------------------------------------------------------------------


def test_oversized_line_with_multiple_words_still_splits_normally():
    # 50 short words on one line, each individually tiny: normal word-boundary
    # splitting must still succeed without error.
    line = " ".join(f"w{i}" for i in range(50))
    text = "## A\n\n" + line
    section = _section(text=text)
    document = _document((section,))
    config = ChunkingConfig(target_tokens=10, max_tokens=20, overlap_tokens=0)
    chunks = chunk_document(document, config=config, token_counter=_word_counter)
    assert len(chunks) > 1
    assert all(_word_counter(c.chunk_text) <= config.max_tokens for c in chunks)


def _huge_single_token_counter(text: str) -> int:
    """Synthetic token_counter where any text containing the marker
    substring 'HUGE_TOKEN' is reported as enormous, regardless of its
    actual character length — used to force the indivisible-segment edge
    case deterministically, independent of the default heuristic.
    """
    if "HUGE_TOKEN" in text:
        return 10_000
    return len(text.split())


def test_single_indivisible_segment_exceeding_max_raises_chunking_error():
    text = "## A\n\n" + "HUGE_TOKEN_SEGMENT_WITH_NO_SPACES"
    section = _section(text=text)
    document = _document((section,))
    config = ChunkingConfig(target_tokens=10, max_tokens=20, overlap_tokens=0)
    with pytest.raises(ChunkingError):
        chunk_document(document, config=config, token_counter=_huge_single_token_counter)


def test_indivisible_segment_error_contains_line_number():
    text = "## A\n\n" + "HUGE_TOKEN_SEGMENT"
    section = _section(text=text, line_start=1)
    document = _document((section,))
    config = ChunkingConfig(target_tokens=10, max_tokens=20, overlap_tokens=0)
    # "## A" is line 1, blank line 2, the oversized segment is line 3.
    with pytest.raises(ChunkingError) as exc_info:
        chunk_document(document, config=config, token_counter=_huge_single_token_counter)
    assert "3" in str(exc_info.value)


def test_indivisible_segment_error_contains_max_tokens():
    text = "## A\n\n" + "HUGE_TOKEN_SEGMENT"
    section = _section(text=text)
    document = _document((section,))
    config = ChunkingConfig(target_tokens=10, max_tokens=20, overlap_tokens=0)
    with pytest.raises(ChunkingError) as exc_info:
        chunk_document(document, config=config, token_counter=_huge_single_token_counter)
    assert "20" in str(exc_info.value)


def test_indivisible_segment_error_is_not_the_generic_internal_error():
    text = "## A\n\n" + "HUGE_TOKEN_SEGMENT"
    section = _section(text=text)
    document = _document((section,))
    config = ChunkingConfig(target_tokens=10, max_tokens=20, overlap_tokens=0)
    with pytest.raises(ChunkingError) as exc_info:
        chunk_document(document, config=config, token_counter=_huge_single_token_counter)
    assert "Internal error" not in str(exc_info.value)
    assert "Indivisible lexical segment" in str(exc_info.value)


def test_no_oversized_chunk_is_silently_emitted_for_indivisible_segment():
    text = "## A\n\n" + "HUGE_TOKEN_SEGMENT"
    section = _section(text=text)
    document = _document((section,))
    config = ChunkingConfig(target_tokens=10, max_tokens=20, overlap_tokens=0)
    with pytest.raises(ChunkingError):
        chunks = chunk_document(document, config=config, token_counter=_huge_single_token_counter)
        # If no exception were raised, this would have to hold — kept here
        # for documentation purposes even though it is unreachable.
        assert all(_huge_single_token_counter(c.chunk_text) <= config.max_tokens for c in chunks)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_chunk_document_raises_on_empty_section_text():
    bad_section = ParsedSection(
        heading_level=2,
        section_path="A",
        section_title="A",
        line_start=1,
        line_end=1,
        text="   ",  # whitespace-only, violates the parser's own guarantee
        rule_ids=(),
    )
    document = _document((bad_section,))
    with pytest.raises(ChunkingError):
        chunk_document(document)
