"""Parser for the RAG MVP ingestion pipeline.

Consumes a :class:`~rag.src.ingestion.loader.LoadedDocument` and produces a
:class:`ParsedDocument`, interpreting only the Markdown structure needed by
the future RAG pipeline: the H1 title, the H2+ heading hierarchy, structural
text blocks, line ranges, and Rule ID mentions.

Explicitly OUT of scope for this module (see ``chunker.py``):

- chunk size, overlap, or token counting;
- ``chunk_index`` / ``chunk_text`` / ``content_hash`` / ``chunk_id``;
- embeddings or Vector Search.

Heading conventions implemented here (see ``rag/README.md`` — Ingestion
Contract, FINAL / CLOSED):

- H1 = ``document_title``. There must be exactly one H1. It never becomes a
  ``ParsedSection`` and never integrates ``section_path``/``section_title``.
- H2-H6 form the structural hierarchy used in ``section_path``, joined with
  the official separator ``" > "``.
- Each H2-H6 heading starts a new, flat ``ParsedSection`` whose text runs
  from that heading line up to (but not including) the next H2-H6 heading
  line — it does NOT include the text of nested/child headings.
- Content after the H1 and before the first H2+ heading becomes one
  introductory ``ParsedSection`` with ``heading_level``/``section_path``/
  ``section_title`` all ``None``, provided it is not blank-only.
- ATX headings only (``#`` .. ``######``), up to 3 leading spaces, requiring
  whitespace (or end of line) after the hash run so that a real heading is
  distinguished from arbitrary text starting with ``#``. Setext headings are
  not supported.
- Headings written inside fenced code blocks (opened with ``` ``` `` or
  ``~~~``) are not treated as structural headings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .loader import LoadedDocument

__all__ = [
    "ParsedSection",
    "ParsedDocument",
    "ParserError",
    "parse_document",
]


class ParserError(ValueError):
    """Raised when a document's Markdown structure violates the parser's contract."""


@dataclass(frozen=True)
class ParsedSection:
    """One flat structural block: either the H1-to-first-H2+ introduction,
    or a single H2-H6 heading plus the text directly under it (not
    including deeper/sibling headings' own text).
    """

    heading_level: int | None
    section_path: str | None
    section_title: str | None
    line_start: int
    line_end: int
    text: str
    rule_ids: tuple[str, ...]


@dataclass(frozen=True)
class ParsedDocument:
    source: LoadedDocument
    document_title: str
    sections: tuple[ParsedSection, ...]


# ---------------------------------------------------------------------------
# Regexes
# ---------------------------------------------------------------------------

# ATX heading: up to 3 leading spaces, 1-6 '#', then whitespace+title or EOL.
_ATX_HEADING_RE = re.compile(r"^ {0,3}(?P<hashes>#{1,6})(?:[ \t]+(?P<title>.*?))?[ \t]*$")

# Opening/closing fence: up to 3 leading spaces, then 3+ backticks or tildes.
_FENCE_RE = re.compile(r"^ {0,3}(?P<marker>`{3,}|~{3,})")

_TRAILING_HASHES_RE = re.compile(r"[ \t]+#+[ \t]*$")

_SECTION_PATH_SEPARATOR = " > "

_RULE_ID_RE = re.compile(r"\bGOLD-\d{3}\b")


# ---------------------------------------------------------------------------
# Fenced code block detection
# ---------------------------------------------------------------------------


def _compute_fenced_mask(lines: list[str]) -> list[bool]:
    """Return a per-line boolean mask: True if that line lies inside (or is a
    delimiter of) a fenced code block, and therefore must not be scanned for
    structural headings.
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

        # Inside a fence: this line is masked regardless of content.
        mask[i] = True
        if match:
            marker = match.group("marker")
            closes = marker[0] == fence_char and len(marker) >= fence_len
            # A closing fence line must contain nothing else but the marker
            # (already guaranteed by _FENCE_RE only matching the prefix here,
            # so we additionally require the rest of the line is blank).
            if closes and line[match.end() :].strip() == "":
                fence_char = None
                fence_len = 0

    return mask


# ---------------------------------------------------------------------------
# Heading extraction
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _HeadingToken:
    line_index: int  # 0-based index into `lines`
    level: int
    title: str


def _extract_headings(lines: list[str], fenced_mask: list[bool]) -> list[_HeadingToken]:
    headings: list[_HeadingToken] = []
    for i, line in enumerate(lines):
        if fenced_mask[i]:
            continue
        match = _ATX_HEADING_RE.match(line)
        if not match:
            continue
        level = len(match.group("hashes"))
        raw_title = match.group("title") or ""
        title = _TRAILING_HASHES_RE.sub("", raw_title).strip()
        headings.append(_HeadingToken(line_index=i, level=level, title=title))
    return headings


# ---------------------------------------------------------------------------
# Block trimming (blank-line trimming at block borders only)
# ---------------------------------------------------------------------------


def _trim_blank_borders(
    lines: list[str], start_index: int, end_index_exclusive: int
) -> tuple[int, int] | None:
    """Given a half-open [start_index, end_index_exclusive) range of 0-based
    line indices, trim blank lines from both borders. Returns the trimmed
    (start_index, end_index_exclusive) or None if the range is blank-only.

    Internal blank lines are never removed.
    """
    start = start_index
    end = end_index_exclusive
    while start < end and lines[start].strip() == "":
        start += 1
    while end > start and lines[end - 1].strip() == "":
        end -= 1
    if start >= end:
        return None
    return start, end


def _extract_rule_ids(text: str) -> tuple[str, ...]:
    found = _RULE_ID_RE.findall(text)
    # Deduplicate while preserving the order of first occurrence in the
    # source text (not alphabetical/numerical order). dict.fromkeys() keeps
    # only the first insertion for each key and preserves insertion order.
    return tuple(dict.fromkeys(found))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_document(document: LoadedDocument) -> ParsedDocument:
    """Parse a :class:`LoadedDocument` into a :class:`ParsedDocument`.

    Raises:
        ParserError: the document does not contain exactly one H1, or
            contains non-empty (non-whitespace) content before that H1.
    """
    lines = document.text.split("\n")
    fenced_mask = _compute_fenced_mask(lines)
    headings = _extract_headings(lines, fenced_mask)

    h1_headings = [h for h in headings if h.level == 1]
    if len(h1_headings) != 1:
        raise ParserError(
            f"Document '{document.source_path}' must contain exactly one H1 "
            f"heading; found {len(h1_headings)}."
        )
    h1 = h1_headings[0]
    document_title = h1.title

    # Blank lines before the H1 are tolerated; any non-blank content is a
    # contract violation and must fail loudly rather than being silently
    # dropped or turned into a pre-H1 section.
    for line_index in range(h1.line_index):
        if lines[line_index].strip() != "":
            raise ParserError(
                f"Document '{document.source_path}' contains non-empty "
                f"content before H1 at line {line_index + 1}."
            )

    structural_headings = [h for h in headings if h.level >= 2]

    sections: list[ParsedSection] = []

    # --- Introductory block: after H1, before the first H2+ heading. ---
    intro_end_exclusive = (
        structural_headings[0].line_index if structural_headings else len(lines)
    )
    trimmed_intro = _trim_blank_borders(lines, h1.line_index + 1, intro_end_exclusive)
    if trimmed_intro is not None:
        start, end = trimmed_intro
        text = "\n".join(lines[start:end])
        sections.append(
            ParsedSection(
                heading_level=None,
                section_path=None,
                section_title=None,
                line_start=start + 1,
                line_end=end,
                text=text,
                rule_ids=_extract_rule_ids(text),
            )
        )

    # --- One flat block per H2-H6 heading. ---
    # Stack of (level, title) for currently-active ancestors (H1 excluded).
    active_ancestors: list[tuple[int, str]] = []

    for idx, heading in enumerate(structural_headings):
        block_start = heading.line_index
        block_end_exclusive = (
            structural_headings[idx + 1].line_index
            if idx + 1 < len(structural_headings)
            else len(lines)
        )

        # Update the ancestor stack: drop any active heading at a level >=
        # this heading's level (they are no longer active ancestors), then
        # push this heading. Real ancestors that remain shallower are kept
        # as-is — no artificial intermediate levels are ever synthesized.
        while active_ancestors and active_ancestors[-1][0] >= heading.level:
            active_ancestors.pop()
        active_ancestors.append((heading.level, heading.title))

        section_path = _SECTION_PATH_SEPARATOR.join(
            title for _, title in active_ancestors
        )

        trimmed = _trim_blank_borders(lines, block_start, block_end_exclusive)
        if trimmed is None:
            # A heading line is never blank, so this should not happen, but
            # guard defensively rather than silently drop a heading.
            trimmed = (block_start, block_start + 1)
        start, end = trimmed
        text = "\n".join(lines[start:end])

        sections.append(
            ParsedSection(
                heading_level=heading.level,
                section_path=section_path,
                section_title=heading.title,
                line_start=start + 1,
                line_end=end,
                text=text,
                rule_ids=_extract_rule_ids(text),
            )
        )

    return ParsedDocument(
        source=document,
        document_title=document_title,
        sections=tuple(sections),
    )
