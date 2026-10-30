"""The parsing layer's common output: whether the file is Markdown, docx or PDF, it becomes the
same sequence of typed elements.

Why parse into typed elements instead of one plain-text string:
  - chunking splits by heading and by SOP step, so it has to know which line is a heading and
    which lines belong to one list item
  - every chunk carries its heading path (e.g. Account Restriction Handling > Procedure), which
    retrieval and citations both use
  - tables have to stay rows and columns; flattened into lines of text, nobody can tell which
    number belongs to which column

Chunking depends only on these types, not on the original file format.
"""
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, Protocol


class ElementKind(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"   # most SOP steps are numbered list items
    TABLE = "table"


@dataclass(frozen=True)
class Element:
    kind: ElementKind
    text: str
    # heading: heading level, 1 is the top level; list_item: nesting level, 1 is outermost; 0 otherwise
    level: int = 0
    ordered: bool = False                    # list_item: True for a numbered list, False for bullets
    rows: tuple[tuple[str, ...], ...] = ()   # table: text of every cell, row by row; the first row is the header
    page: int | None = None                  # page number, PDF only
    heading_path: tuple[str, ...] = ()       # enclosing headings, outermost first; a heading does not include itself


@dataclass(frozen=True)
class ParsedDocument:
    elements: tuple[Element, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)  # Markdown frontmatter; empty for other formats


class DocumentParseError(Exception):
    """The file itself is the problem: unsupported format, corrupt file, wrong encoding, a PDF
    without a text layer... Retrying the same file always gives the same result, so the consumer
    treats it as a permanent error."""


class DocumentParser(Protocol):
    """Takes the file's bytes, not a path: the file may live on S3 later, and the parser should
    not care where it came from."""

    def parse(self, data: bytes) -> ParsedDocument: ...


def finalize(elements: list[Element], metadata: Mapping[str, Any] | None = None) -> ParsedDocument:
    """The last step shared by all three parsers: normalize heading levels and compute every
    element's heading path.

    Heading levels are re-ranked by relative order: the levels that occur in the document,
    from highest to lowest, become 1, 2, 3. A Markdown file that only uses ## and ###, and a PDF
    with the same structure where only "large font / smaller font" is visible, end up with the
    same levels; Word's "Title" style (one above "Heading 1") also slots in naturally.
    """
    if not elements:
        raise DocumentParseError("no content found in the document")
    levels = sorted({e.level for e in elements if e.kind is ElementKind.HEADING})
    rank = {level: i + 1 for i, level in enumerate(levels)}

    path: list[tuple[int, str]] = []   # headings enclosing the current position: (level, text), outermost first
    result = []
    for element in elements:
        if element.kind is ElementKind.HEADING:
            level = rank[element.level]
            while path and path[-1][0] >= level:   # a heading of the same or a higher level closes the previous sections
                path.pop()
            result.append(replace(element, level=level, heading_path=tuple(text for _, text in path)))
            path.append((level, element.text))
        else:
            result.append(replace(element, heading_path=tuple(text for _, text in path)))
    return ParsedDocument(elements=tuple(result), metadata=dict(metadata or {}))


def table_element(rows: list[tuple[str, ...]], page: int | None = None) -> Element:
    # text is a readable version for people and for embedding later; rows is the structure
    text = "\n".join(" | ".join(row) for row in rows)
    return Element(kind=ElementKind.TABLE, text=text, rows=tuple(rows), page=page)


_SPACES = re.compile(r"[^\S\n]+")   # runs of whitespace other than newlines


def join_lines(lines: Iterable[str]) -> str:
    """Join a paragraph that was wrapped over several lines back into one line, with single spaces.
    Both manual line breaks in Markdown and automatic wrapping in PDFs come through here."""
    return " ".join(line for line in (_SPACES.sub(" ", line).strip() for line in lines) if line)


def clean_text(text: str) -> str:
    """Strip surrounding whitespace, collapse runs of whitespace, join wrapped lines with join_lines."""
    return join_lines(text.splitlines())
