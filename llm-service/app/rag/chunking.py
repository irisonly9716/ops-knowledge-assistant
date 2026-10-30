"""Parsed elements -> chunks ready for embedding.

How a document is cut:
  - never across a heading: every chunk belongs to exactly one section, and its heading path says which
  - inside a section, elements are packed in order until the token limit. A step (a top-level
    numbered list item) travels together with its nested items and is never split between chunks
  - only a unit that is larger than the limit on its own is cut further: a table by rows (every piece
    repeats the header row), text by LangChain's RecursiveCharacterTextSplitter, with overlap
  - every chunk starts with a header (document type, department, campaign, start date, host, section)
    that is embedded together with the text, a "contextual chunk header": "Wait for the review: it
    usually takes 1-3 business days" means little without "Account Restriction Handling > Procedure"
List numbers are written back ("3. Collect appeal evidence"): parsing drops them because docx and PDF
don't store them as text, and without them "step 3" in a question has nothing to match.

Lengths are counted in tokens of the embedding model's tokenizer (tiktoken), not in characters: the
embedding API's limit and its price are both in tokens. tiktoken downloads its 1.7 MB encoding file on
first use and caches it; a deployment image should download it at build time.
"""
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from functools import cache
from typing import Any

import tiktoken
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.rag.parsing import Element, ElementKind, ParsedDocument

# A starting point, to be tuned with the eval: every sample SOP section fits, while one chunk stays
# about one topic. text-embedding-3-small itself accepts up to 8,191 tokens
MAX_CHUNK_TOKENS = 600
# About 13% of the limit. Only applies inside a unit too large for one chunk; the splitter overlaps by
# whole sentences, so the real overlap is the last sentence(s) that fit into this budget
OVERLAP_TOKENS = 80
TOKEN_ENCODING = "cl100k_base"   # tiktoken.encoding_for_model("text-embedding-3-small")
# Coarse to fine: paragraphs, lines, sentences, clauses, words, characters
SEPARATORS = ["\n\n", "\n", ". ", "; ", ", ", " ", ""]
INDENT = "   "   # nested list items in the chunk text, as in Markdown


@dataclass(frozen=True)
class DocumentInfo:
    """What the header says about the document. Comes from the document_catalog view, which also
    decides the labels ("SOP", "All departments"...), so they exist in one place only."""
    type_label: str
    department_label: str
    title: str
    campaign_name: str | None = None
    start_date: date | None = None
    host_name: str | None = None


@dataclass(frozen=True)
class Chunk:
    index: int                     # position in the document, from 0 (doc_chunks.chunk_index)
    content: str                   # header + text: exactly what is embedded, stored and shown to the LLM
    heading_path: tuple[str, ...]
    token_count: int
    pages: tuple[int, ...] = ()    # PDF only

    def metadata(self) -> dict[str, Any]:
        """doc_chunks.metadata. Only what belongs to the chunk itself; doc_type, campaign_id and the
        rest live on documents and are joined when needed, not copied onto every chunk."""
        return {"heading_path": list(self.heading_path), "pages": list(self.pages), "token_count": self.token_count}


@dataclass
class _Unit:
    """The smallest piece that is never split between chunks unless it is too large on its own:
    a paragraph, a table, or a list item together with its nested items."""
    text: str
    is_list: bool = False
    table: Element | None = None
    step: int | None = None        # set for a top-level numbered list item: its number in the list
    item: str = ""                 # the list item's own text, without nested items
    subitems: list[str] = field(default_factory=list)
    pages: set[int] = field(default_factory=set)


@cache
def _encoding() -> tiktoken.Encoding:
    return tiktoken.get_encoding(TOKEN_ENCODING)


def count_tokens(text: str) -> int:
    return len(_encoding().encode(text))


def chunk_header(info: DocumentInfo, heading_path: tuple[str, ...]) -> str:
    facts = [info.type_label, info.department_label, info.campaign_name,
             info.start_date.isoformat() if info.start_date else None,
             f"Host: {info.host_name}" if info.host_name else None]
    location = " > ".join(heading_path) if heading_path else info.title
    return " | ".join(fact for fact in facts if fact) + "\n" + location


def chunk_document(document: ParsedDocument, info: DocumentInfo, *,
                   max_tokens: int = MAX_CHUNK_TOKENS, overlap_tokens: int = OVERLAP_TOKENS) -> list[Chunk]:
    chunks: list[Chunk] = []
    for heading_path, elements in _sections(document):
        header = chunk_header(info, heading_path)
        budget = max_tokens - count_tokens(header + "\n\n")
        if budget <= 0:
            raise ValueError(f"the header alone exceeds {max_tokens} tokens: {header!r}")
        for units in _pack(_units(elements), budget, overlap_tokens):
            content = header + "\n\n" + _join(units)
            chunks.append(Chunk(index=len(chunks), content=content, heading_path=heading_path,
                                token_count=count_tokens(content),
                                pages=tuple(sorted(set().union(*(unit.pages for unit in units))))))
    return chunks


def sop_steps(document: ParsedDocument) -> list[dict[str, Any]]:
    """documents.steps for an SOP: every top-level numbered list item, numbered within its list, with
    its nested items, so "what is the last step" can be answered without a vector search."""
    return [{"section": " > ".join(heading_path), "order": unit.step, "text": unit.item, "substeps": unit.subitems}
            for heading_path, elements in _sections(document)
            for unit in _units(elements) if unit.step is not None]


def _sections(document: ParsedDocument) -> Iterator[tuple[tuple[str, ...], list[Element]]]:
    """Consecutive non-heading elements under the same headings. Headings themselves are not chunk
    text; they appear in the header."""
    path: tuple[str, ...] | None = None
    elements: list[Element] = []
    for element in document.elements:
        if element.kind is ElementKind.HEADING:
            continue
        if element.heading_path != path and elements:
            yield path, elements
            elements = []
        path = element.heading_path
        elements.append(element)
    if elements:
        yield path, elements


def _units(elements: list[Element]) -> list[_Unit]:
    units: list[_Unit] = []
    numbers: list[int] = []   # the running number at each nesting level of the current list
    for element in elements:
        pages = {element.page} if element.page is not None else set()
        if element.kind is not ElementKind.LIST_ITEM:
            numbers = []      # anything else ends the list; the next list starts again at 1
            if element.kind is ElementKind.TABLE:
                units.append(_Unit(_table_text(element.rows), table=element, pages=pages))
            else:
                units.append(_Unit(element.text, pages=pages))
            continue

        depth = element.level
        del numbers[depth:]   # back from a nested list: its numbering ends
        numbers += [0] * (depth - len(numbers))
        numbers[depth - 1] += 1
        marker = f"{numbers[depth - 1]}." if element.ordered else "-"
        line = INDENT * (depth - 1) + f"{marker} {element.text}"
        parent = units[-1] if units and units[-1].is_list else None
        if depth > 1 and parent is not None:
            parent.text += "\n" + line
            parent.subitems.append(element.text)
            parent.pages |= pages
        else:
            units.append(_Unit(line, is_list=True, item=element.text, pages=pages,
                               step=numbers[0] if depth == 1 and element.ordered else None))
    return units


def _table_text(rows: tuple[tuple[str, ...], ...]) -> str:
    """A Markdown table: LLMs read it reliably, and every row keeps its column order."""
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * len(rows[0])]
    return "\n".join(lines + ["| " + " | ".join(row) + " |" for row in rows[1:]])


def _join(units: list[_Unit]) -> str:
    text = units[0].text
    for previous, unit in zip(units, units[1:]):
        text += ("\n" if previous.is_list and unit.is_list else "\n\n") + unit.text
    return text


def _pack(units: list[_Unit], budget: int, overlap_tokens: int) -> Iterator[list[_Unit]]:
    """Fill each chunk with as many whole units as fit; a unit that doesn't fit starts the next chunk."""
    current: list[_Unit] = []
    for unit in units:
        for piece in _fit(unit, budget, overlap_tokens):
            if current and count_tokens(_join([*current, piece])) > budget:
                yield current
                current = []
            current.append(piece)
    if current:
        yield current


def _fit(unit: _Unit, budget: int, overlap_tokens: int) -> list[_Unit]:
    """The unit itself if it fits; otherwise pieces that each fit."""
    if count_tokens(unit.text) <= budget:
        return [unit]
    if unit.table is not None and len(unit.table.rows) > 2:
        return _split_table(unit, budget, overlap_tokens)
    splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
        encoding_name=TOKEN_ENCODING, chunk_size=budget, chunk_overlap=min(overlap_tokens, budget // 2),
        separators=SEPARATORS, keep_separator="end")
    return [_Unit(piece, is_list=unit.is_list, pages=unit.pages) for piece in splitter.split_text(unit.text)]


def _split_table(unit: _Unit, budget: int, overlap_tokens: int) -> list[_Unit]:
    """Rows in order, as many per piece as fit, each piece repeating the header row: a row without its
    header is a list of numbers nobody can read."""
    header, *rows = unit.table.rows
    pieces: list[_Unit] = []
    batch: list[tuple[str, ...]] = []
    for row in rows:
        if batch and count_tokens(_table_text((header, *batch, row))) > budget:
            pieces.append(_Unit(_table_text((header, *batch)), pages=unit.pages))
            batch = []
        batch.append(row)
    pieces.append(_Unit(_table_text((header, *batch)), pages=unit.pages))
    # A single row too large for a chunk falls back to splitting its text
    return [small for piece in pieces for small in _fit(piece, budget, overlap_tokens)]
