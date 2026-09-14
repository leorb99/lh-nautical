"""Chunker for the RAG MVP ingestion pipeline — Chunking Strategy v1.

Consumes a :class:`~src.ingestion.parser.ParsedDocument` (which itself wraps
a :class:`~src.ingestion.loader.LoadedDocument`) and produces an in-memory
``tuple[Chunk, ...]``. Does not redefine anything already decided by
``loader.py`` or ``parser.py``; it only decides how ``ParsedSection`` objects
become retrieval units.

Core principle
--------------

The primary semantic unit is the ``ParsedSection`` produced by the parser.
Fixed-size chunking is never applied blindly:

    1 ParsedSection -> 1 Chunk

is the default. A section is only split internally when its estimated token
count exceeds ``ChunkingConfig.max_tokens``. ``target_tokens`` is a
*preference*, never an obligation — a coherent section of, say, 700
estimated tokens is kept whole when the hard max is 800.

A structural ``ParsedSection`` whose text is only its own heading line
(optionally followed by blank/whitespace-only lines) produces NO chunk —
the parser still preserves it as structure (for ``section_path``
construction), but it carries no retrievable content of its own. This is a
structural check (is there any non-blank content after the heading line?),
never a token-count heuristic — a short-but-real paragraph is always kept.

Token counting
--------------

No embedding model has been selected yet for this project, so this module
never imports or depends on a specific tokenizer (tiktoken, Hugging Face,
Anthropic's, OpenAI's, etc.) and adds no external dependency. Instead, token
counting is an injectable abstraction:

    TokenCounter = Callable[[str], int]

:func:`estimate_token_count` is the default implementation: a deterministic,
stdlib-only lexical heuristic (word-groups and standalone punctuation/symbol
characters). It is explicitly NOT a real tokenizer for any embedding model.
The 512/800/64 budgets in :class:`ChunkingConfig` are *estimates* under this
heuristic. When an embedding model is chosen, this same chunker must accept
an exact tokenizer via the ``token_counter`` parameter with no redesign of
the splitting/packing/overlap algorithm itself.

Explicitly OUT of scope for this module (see the future persistence /
retrieval / generation / evaluation modules):

- Delta writes, Spark DataFrames, MERGE, Volumes, Change Data Feed
  operations;
- Databricks AI Search endpoints/indexes, embeddings, hybrid retrieval,
  reranking;
- Claude, any API, any evaluation pipeline.

This module produces a ``tuple[Chunk, ...]`` in memory and stops there.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable, Iterable

from .loader import compute_document_hash, normalize_text
from .parser import ParsedDocument, ParsedSection

__all__ = [
    "TokenCounter",
    "ChunkingConfig",
    "DEFAULT_CHUNKING_CONFIG",
    "Chunk",
    "ChunkingError",
    "estimate_token_count",
    "chunk_document",
    "chunk_documents",
]


class ChunkingError(ValueError):
    """Raised for invalid configuration or an unrecoverable chunking state."""


# ---------------------------------------------------------------------------
# Token counting (approximate, injectable, stdlib-only)
# ---------------------------------------------------------------------------

TokenCounter = Callable[[str], int]

# Word-groups (allowing an internal hyphen/apostrophe join) OR a single
# standalone punctuation/symbol character. Deterministic, Unicode-aware,
# stdlib `re` only. This is a lexical estimate, not a real tokenizer.
_TOKEN_PATTERN = re.compile(r"\w+(?:['’\-]\w+)*|[^\s\w]", re.UNICODE)


def estimate_token_count(text: str) -> int:
    """Deterministic, approximate token count using only the standard library.

    NOT a tokenizer for any specific embedding model — see the module
    docstring. Useful for chunking v1, testable, and trivially replaceable
    by an exact tokenizer later via the ``token_counter`` parameter.
    """
    return len(_TOKEN_PATTERN.findall(text))


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChunkingConfig:
    """Immutable configuration for Chunking Strategy v1.

    ``target_tokens`` is a preference (sections at or below ``max_tokens``
    are never split just for exceeding ``target_tokens``). ``max_tokens`` is
    the hard ceiling that triggers an internal split. ``overlap_tokens`` is
    the approximate budget for intra-section overlap between consecutive
    subchunks of the *same* ``ParsedSection`` — overlap never crosses a
    section boundary and is never forced past a natural block boundary.
    """

    target_tokens: int = 512
    max_tokens: int = 800
    overlap_tokens: int = 64

    def __post_init__(self) -> None:
        if self.target_tokens <= 0:
            raise ChunkingError("target_tokens must be > 0")
        if self.max_tokens <= 0:
            raise ChunkingError("max_tokens must be > 0")
        if self.overlap_tokens < 0:
            raise ChunkingError("overlap_tokens must be >= 0")
        if self.target_tokens > self.max_tokens:
            raise ChunkingError("target_tokens must be <= max_tokens")
        if self.overlap_tokens >= self.target_tokens:
            raise ChunkingError("overlap_tokens must be < target_tokens")


DEFAULT_CHUNKING_CONFIG = ChunkingConfig()


# ---------------------------------------------------------------------------
# Chunk — the future Delta row's in-memory counterpart (minus ingested_at
# and any embedding, which do not belong to a chunk's logical identity).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    chunk_index: int
    document_id: str
    document_title: str
    source_path: str
    canonical_domain: str
    document_status: str
    section_path: str | None
    section_title: str | None
    chunk_text: str
    rule_ids: tuple[str, ...]
    line_start: int
    line_end: int
    content_hash: str
    document_hash: str
    source_git_commit: str | None


# ---------------------------------------------------------------------------
# Rule ID extraction on the FINAL chunk text (never inherited blindly from
# ParsedSection.rule_ids — a split chunk may legitimately drop or, via
# overlap, duplicate a Rule ID relative to its parent section).
# ---------------------------------------------------------------------------

_RULE_ID_RE = re.compile(r"\bGOLD-\d{3}\b")


def _extract_rule_ids_from_chunk(text: str) -> tuple[str, ...]:
    found = _RULE_ID_RE.findall(text)
    # First-occurrence order preserved, duplicates removed — mirrors the
    # exact contract already enforced in parser.py's own Rule ID extraction.
    return tuple(dict.fromkeys(found))


# ---------------------------------------------------------------------------
# chunk_id — deterministic SHA-256 over a canonical JSON payload.
# ---------------------------------------------------------------------------


def _compute_chunk_id(
    document_id: str, section_path: str | None, chunk_index: int, content_hash: str
) -> str:
    payload = json.dumps(
        [document_id, section_path or "", chunk_index, content_hash],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    # compute_document_hash is exactly sha256(text.encode("utf-8")).hexdigest();
    # reused here rather than re-implementing hashing.
    return compute_document_hash(payload)


# ---------------------------------------------------------------------------
# Atomic units: the finest structural building blocks used when a section
# must be split. A unit is either:
#   - a fenced code block (kept whole whenever it fits under max_tokens);
#   - a blank-line-delimited block of contiguous lines (paragraph, list,
#     table — no CommonMark-complete parsing is attempted; blank lines are
#     the only paragraph/list/table boundary this module needs, since real
#     tables/lists in the canonical documents never contain blank lines);
#   - as a last-resort fallback, a single source line, or (rarer still) a
#     lexical (word-boundary) slice of one line that alone exceeds
#     max_tokens.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Unit:
    line_start: int  # absolute, 1-indexed, into the document's normalized text
    line_end: int  # absolute, 1-indexed, inclusive
    text: str
    is_synthetic: bool = field(default=False)
    """True only for word-boundary slices of a single oversized line, where
    ``text`` cannot be recovered by slicing the original line-indexed source
    (multiple synthetic units may then share the same line_start/line_end).
    """


_FENCE_RE = re.compile(r"^ {0,3}(?P<marker>`{3,}|~{3,})")


def _compute_local_fence_mask(lines: list[str]) -> list[bool]:
    """Per-line fence mask, mirroring parser.py's own (private) fence
    detection. Reimplemented locally rather than importing parser's private
    helper, to keep chunker.py independent of parser.py's internals while
    applying the identical, already-approved fence rule.
    """
    mask = [False] * len(lines)
    fence_char: str | None = None
    fence_len = 0
    for i, line in enumerate(lines):
        match = _FENCE_RE.match(line)
        if fence_char is None:
            if match:
                marker = match.group("marker")
                fence_char = marker[0]
                fence_len = len(marker)
                mask[i] = True
            continue
        mask[i] = True
        if match:
            marker = match.group("marker")
            closes = marker[0] == fence_char and len(marker) >= fence_len
            if closes and line[match.end() :].strip() == "":
                fence_char = None
                fence_len = 0
    return mask


def _make_unit(section: ParsedSection, local_lines: list[str], local_start: int, local_end: int) -> _Unit:
    text = "\n".join(local_lines[local_start:local_end])
    return _Unit(
        line_start=section.line_start + local_start,
        line_end=section.line_start + local_end - 1,
        text=text,
    )


def _derive_atomic_units(section: ParsedSection) -> list[_Unit]:
    local_lines = section.text.split("\n")
    fence_mask = _compute_local_fence_mask(local_lines)
    units: list[_Unit] = []
    i = 0
    n = len(local_lines)
    while i < n:
        if fence_mask[i]:
            j = i
            while j < n and fence_mask[j]:
                j += 1
            units.append(_make_unit(section, local_lines, i, j))
            i = j
        elif local_lines[i].strip() == "":
            i += 1
        else:
            j = i
            while j < n and local_lines[j].strip() != "" and not fence_mask[j]:
                j += 1
            units.append(_make_unit(section, local_lines, i, j))
            i = j
    return units


def _split_line_lexically(
    line_text: str, absolute_line_number: int, token_counter: TokenCounter, config: ChunkingConfig
) -> list[_Unit]:
    """Last-resort fallback: word-boundary split of a single source line
    whose own token count alone exceeds ``max_tokens``. All resulting pieces
    share the same ``line_start == line_end`` (the original line), since a
    sub-line slice has no line of its own in the source — this preserves
    honest provenance (cites the correct source line) without inventing
    line numbers that do not exist in the document.

    If a single whitespace-delimited segment is itself indivisible (no
    internal whitespace to split on) and its own token count already
    exceeds ``max_tokens``, this function raises :class:`ChunkingError`
    explicitly rather than silently emitting an oversized unit — sub-word
    character-offset splitting is deliberately out of scope for this MVP,
    since it would require inventing provenance that does not exist in the
    source (see module docstring).
    """
    words = line_text.split(" ")
    pieces: list[str] = []
    current: list[str] = []
    for word in words:
        word_tokens = token_counter(word)
        if word_tokens > config.max_tokens:
            raise ChunkingError(
                f"Indivisible lexical segment at source line "
                f"{absolute_line_number} has an estimated {word_tokens} "
                f"tokens, exceeding max_tokens={config.max_tokens}. This "
                "segment contains no whitespace to split on further; "
                "splitting it by character offset would invent provenance "
                "that does not exist in the source, which this chunker "
                "deliberately refuses to do."
            )
        candidate_words = current + [word]
        if current and token_counter(" ".join(candidate_words)) > config.max_tokens:
            pieces.append(" ".join(current))
            current = [word]
        else:
            current = candidate_words
    if current:
        pieces.append(" ".join(current))
    if not pieces:
        raise ChunkingError(
            f"Unable to lexically split line {absolute_line_number}: no words found."
        )
    return [
        _Unit(
            line_start=absolute_line_number,
            line_end=absolute_line_number,
            text=piece,
            is_synthetic=True,
        )
        for piece in pieces
    ]


def _split_unit_by_lines(unit: _Unit, token_counter: TokenCounter, config: ChunkingConfig) -> list[_Unit]:
    """Fallback for an oversized atomic unit (typically a fenced block, a
    table, or a paragraph/list larger than max_tokens): split it by
    individual source lines, greedily packed up to max_tokens each.
    """
    local_lines = unit.text.split("\n")
    sub_units: list[_Unit] = []
    i = 0
    n = len(local_lines)
    while i < n:
        line_text = local_lines[i]
        line_abs = unit.line_start + i
        if token_counter(line_text) > config.max_tokens:
            sub_units.extend(_split_line_lexically(line_text, line_abs, token_counter, config))
            i += 1
            continue
        j = i + 1
        while j < n:
            candidate_text = "\n".join(local_lines[i : j + 1])
            if token_counter(candidate_text) > config.max_tokens:
                break
            j += 1
        sub_units.append(
            _Unit(
                line_start=unit.line_start + i,
                line_end=unit.line_start + j - 1,
                text="\n".join(local_lines[i:j]),
            )
        )
        i = j
    return sub_units


def _ensure_units_fit_max(
    units: list[_Unit], token_counter: TokenCounter, config: ChunkingConfig
) -> list[_Unit]:
    result: list[_Unit] = []
    for unit in units:
        if token_counter(unit.text) <= config.max_tokens:
            result.append(unit)
        else:
            # Fenced blocks are only ever split here as an explicit,
            # documented last resort — never merely to approach `target`.
            result.extend(_split_unit_by_lines(unit, token_counter, config))
    return result


# ---------------------------------------------------------------------------
# Text reconstruction: prefer slicing the ORIGINAL section text (preserving
# any inter-unit blank lines verbatim) over joining unit texts directly,
# except when synthetic (sub-line) units are involved, where no such slice
# exists.
# ---------------------------------------------------------------------------


def _reconstruct_group_text(
    section_local_lines: list[str], section_line_start: int, group: list[_Unit]
) -> str:
    if any(u.is_synthetic for u in group):
        return "\n".join(u.text for u in group)
    local_start = group[0].line_start - section_line_start
    local_end = group[-1].line_end - section_line_start + 1
    return "\n".join(section_local_lines[local_start:local_end])


# ---------------------------------------------------------------------------
# Packing: group contiguous atomic units into subchunks, preferring the
# boundary whose resulting token count is closest to `target_tokens`
# without ever exceeding `max_tokens`.
# ---------------------------------------------------------------------------


def _pack_units(
    section_local_lines: list[str],
    section_line_start: int,
    units: list[_Unit],
    token_counter: TokenCounter,
    config: ChunkingConfig,
) -> list[list[_Unit]]:
    groups: list[list[_Unit]] = []
    i = 0
    n = len(units)
    while i < n:
        best_end = i + 1
        best_diff: int | None = None
        j = i
        while j < n:
            candidate = units[i : j + 1]
            candidate_tokens = token_counter(
                _reconstruct_group_text(section_local_lines, section_line_start, candidate)
            )
            if candidate_tokens > config.max_tokens:
                break
            diff = abs(candidate_tokens - config.target_tokens)
            if best_diff is None or diff <= best_diff:
                best_diff = diff
                best_end = j + 1
            j += 1
        if best_diff is None:
            # Precondition violated: _ensure_units_fit_max must guarantee
            # every individual unit already fits under max_tokens.
            raise ChunkingError(
                "Internal error: an atomic unit exceeds max_tokens after "
                "pre-splitting; this should not happen."
            )
        groups.append(units[i:best_end])
        i = best_end
    return groups


# ---------------------------------------------------------------------------
# Overlap: only between subchunks of the SAME ParsedSection, built from
# whole trailing units of the previous group, never forced past the
# overlap budget or the hard max, and never sourced from another section.
# ---------------------------------------------------------------------------


def _compute_overlap_lead_units(
    section_local_lines: list[str],
    section_line_start: int,
    prev_group: list[_Unit],
    current_group: list[_Unit],
    token_counter: TokenCounter,
    config: ChunkingConfig,
) -> list[_Unit]:
    if config.overlap_tokens <= 0 or not prev_group:
        return []

    lead: list[_Unit] = []
    for unit in reversed(prev_group):
        candidate_lead = [unit] + lead
        overlap_only_tokens = token_counter(
            _reconstruct_group_text(section_local_lines, section_line_start, candidate_lead)
        )
        if overlap_only_tokens > config.overlap_tokens:
            break
        full_tokens = token_counter(
            _reconstruct_group_text(
                section_local_lines, section_line_start, candidate_lead + current_group
            )
        )
        if full_tokens > config.max_tokens:
            break
        lead = candidate_lead
    return lead


# ---------------------------------------------------------------------------
# Heading-only sections: a structural ParsedSection whose only content is
# its own heading line (optionally followed by blank/whitespace-only lines)
# carries no retrievable factual content of its own. The parser still
# preserves it (structure is its job), but the chunker deliberately does
# not turn it into a retrieval unit — its section_path/section_title
# already give descendant chunks the hierarchical context it would have
# provided. This is a structural check, not a token-size heuristic: a
# short-but-real paragraph (however few tokens) is never skipped.
# ---------------------------------------------------------------------------


def _is_heading_only(section: ParsedSection) -> bool:
    if section.heading_level is None:
        # The introductory section (heading_level=None) is never subject to
        # this rule, regardless of how short it is.
        return False
    lines = section.text.split("\n")
    return all(line.strip() == "" for line in lines[1:])


# ---------------------------------------------------------------------------
# Per-section chunk ranges
# ---------------------------------------------------------------------------


def _chunk_ranges_for_section(
    section: ParsedSection, token_counter: TokenCounter, config: ChunkingConfig
) -> list[tuple[int, int, str]]:
    """Return a list of (line_start, line_end, chunk_text) for one section."""
    if token_counter(section.text) <= config.max_tokens:
        # Default rule: 1 ParsedSection -> 1 Chunk, verbatim, regardless of
        # whether it exceeds `target_tokens` (target is a preference only).
        return [(section.line_start, section.line_end, section.text)]

    section_local_lines = section.text.split("\n")
    units = _ensure_units_fit_max(_derive_atomic_units(section), token_counter, config)
    if not units:
        raise ChunkingError(
            f"Section '{section.section_title or '(introduction)'}' produced no "
            "atomic units despite non-empty, oversized text."
        )

    groups = _pack_units(section_local_lines, section.line_start, units, token_counter, config)

    results: list[tuple[int, int, str]] = []
    for k, group in enumerate(groups):
        if k == 0:
            combined = group
        else:
            lead = _compute_overlap_lead_units(
                section_local_lines, section.line_start, groups[k - 1], group, token_counter, config
            )
            combined = lead + group
        text = _reconstruct_group_text(section_local_lines, section.line_start, combined)
        results.append((combined[0].line_start, combined[-1].line_end, text))
    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def chunk_document(
    document: ParsedDocument,
    *,
    config: ChunkingConfig = DEFAULT_CHUNKING_CONFIG,
    token_counter: TokenCounter = estimate_token_count,
) -> tuple[Chunk, ...]:
    """Turn a :class:`ParsedDocument` into a deterministic ``tuple[Chunk, ...]``.

    ``chunk_index`` is 0-based and global across the whole document (never
    reset per section, and never gapped by a skipped heading-only section).
    Raises :class:`ChunkingError` for an unexpectedly empty ``ParsedSection``,
    an empty produced chunk, or an invalid line range — all of which indicate
    a bug rather than a normal document state. A document composed entirely
    of heading-only sections legitimately returns ``()`` — that is not an
    error.
    """
    source = document.source
    chunks: list[Chunk] = []
    chunk_index = 0

    for section in document.sections:
        if section.text.strip() == "":
            raise ChunkingError(
                f"Document '{source.source_path}' contains a ParsedSection "
                "with empty text; the parser contract guarantees non-blank "
                "sections, so this indicates an upstream contract violation."
            )

        if _is_heading_only(section):
            # No retrievable content of its own; section_path/section_title
            # already carry its hierarchical context into descendant chunks.
            continue

        for line_start, line_end, chunk_text in _chunk_ranges_for_section(
            section, token_counter, config
        ):
            if chunk_text.strip() == "":
                raise ChunkingError(
                    f"Document '{source.source_path}' produced an empty chunk "
                    f"at lines {line_start}-{line_end}."
                )
            if line_start > line_end:
                raise ChunkingError(
                    f"Document '{source.source_path}' produced an invalid "
                    f"line range ({line_start} > {line_end})."
                )

            content_hash = compute_document_hash(normalize_text(chunk_text))
            chunk_id = _compute_chunk_id(
                source.document_id, section.section_path, chunk_index, content_hash
            )

            chunks.append(
                Chunk(
                    chunk_id=chunk_id,
                    chunk_index=chunk_index,
                    document_id=source.document_id,
                    document_title=document.document_title,
                    source_path=source.source_path,
                    canonical_domain=source.canonical_domain,
                    document_status=source.document_status,
                    section_path=section.section_path,
                    section_title=section.section_title,
                    chunk_text=chunk_text,
                    rule_ids=_extract_rule_ids_from_chunk(chunk_text),
                    line_start=line_start,
                    line_end=line_end,
                    content_hash=content_hash,
                    document_hash=source.document_hash,
                    source_git_commit=source.source_git_commit,
                )
            )
            chunk_index += 1

    return tuple(chunks)


def chunk_documents(
    documents: Iterable[ParsedDocument],
    *,
    config: ChunkingConfig = DEFAULT_CHUNKING_CONFIG,
    token_counter: TokenCounter = estimate_token_count,
) -> tuple[Chunk, ...]:
    """Convenience wrapper: chunk multiple documents, concatenating results.

    Each document's own ``chunk_index`` restarts at 0 (global only within a
    single document, per the ingestion contract).
    """
    result: list[Chunk] = []
    for document in documents:
        result.extend(chunk_document(document, config=config, token_counter=token_counter))
    return tuple(result)
