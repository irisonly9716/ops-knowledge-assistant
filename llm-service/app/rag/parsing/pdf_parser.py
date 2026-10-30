"""PDF -> elements. pdfplumber gives the position and font size of every character; the structure
is inferred from those.

Only "digital" PDFs with a text layer (exported from Word / WPS) are supported. A scanned PDF
contains only images and needs OCR, which is not supported yet.

The big difference from docx: a PDF has no "heading", "paragraph" or "list", only "on page N at
these coordinates, this character was drawn at this size". So all structure is inferred (the
rules follow common Word / WPS layouts):
  line        words that overlap vertically belong to one line (comparing only the top coordinate
              is not enough: inline code uses a smaller font and sits lower)
  heading     font clearly larger than the body text; the larger the font, the higher the level
  list item   the line starts with a number (1.  2)) or a bullet; levels follow the marker's
              horizontal position, further right is deeper
  paragraph   a line close to the previous one that does not start a heading or list item
              continues the current paragraph
  table       pdfplumber finds tables by their ruling lines; a table split across pages is joined
              back into one; a table without borders is not recognized and reads as plain text
Single column, top to bottom. Two-column layouts, headers and footers, and paragraphs separated
only by a first-line indent are not handled.
"""
import re
from collections import Counter
from dataclasses import dataclass
from io import BytesIO

import pdfplumber

from app.rag.parsing.elements import (
    DocumentParseError,
    Element,
    ElementKind,
    ParsedDocument,
    clean_text,
    finalize,
    join_lines,
    table_element,
)

LIST_MARKER = re.compile(
    r"^(?:(?P<number>\d{1,3})(?:\.(?!\d)|\))"   # 1.  2)  -- a decimal such as "1.25" is not a marker
    r"|[•●○◦▪■□]"                        # bullets;  is the dot Word / WPS draw with the Symbol font
    r"|[-*](?=\s))\s*"
)
HEADING_SIZE_RATIO = 1.2        # a heading's font is at least 1.2 times the body size
SAME_ROW_OVERLAP = 0.4          # vertical overlap above 40% of the shorter word's height means the same line
PARAGRAPH_GAP_RATIO = 0.6       # a gap to the previous line above 0.6 of the font size starts a new paragraph
INDENT_TOLERANCE = 3.0          # horizontal positions within 3pt count as aligned (pt = 1/72 inch)
# Word / WPS PDFs contain no space characters; words are told apart by the gap between letters.
# Measured on the fixtures: letters inside a word are at most 0.01 font sizes apart, words at
# least 0.21. pdfplumber's default (a fixed 3pt) glued words together on tightly set 12pt lines,
# where the gap between words was only 2.6pt
WORD_GAP_RATIO = 0.1
SENTENCE_END = tuple(".!?")


@dataclass
class _Line:
    text: str
    x0: float
    top: float
    bottom: float
    size: float
    page: int


@dataclass
class _Table:
    rows: list[tuple[str, ...]]
    top: float
    page: int


class PdfParser:
    def parse(self, data: bytes) -> ParsedDocument:
        try:
            with pdfplumber.open(BytesIO(data)) as pdf:
                pages = [_read_page(page) for page in pdf.pages]
        except Exception as e:   # not a PDF, corrupt, password-protected...
            raise DocumentParseError(f"cannot read the PDF: {e}") from e

        lines = [line for page_lines, _ in pages for line in page_lines]
        tables = [table for _, page_tables in pages for table in page_tables]
        if not lines and not tables:
            raise DocumentParseError("the PDF has no text layer (probably a scan or an image); "
                                     "that needs OCR, which is not supported yet")
        return finalize(_Assembler(lines).run(pages))


def _read_page(page) -> tuple[list[_Line], list[_Table]]:
    tables = []
    table_boxes = []
    for found in page.find_tables():
        rows = [tuple(clean_text(cell or "") for cell in row) for row in found.extract()]
        # A single-column "table" is usually a bordered text box; treat it as text. A single row
        # is kept: it can be the header of a table that continues on the next page
        if rows and len(rows[0]) >= 2:
            tables.append(_Table(rows=rows, top=found.bbox[1], page=page.page_number))
            table_boxes.append(found.bbox)

    # Not extra_attrs=["size"]: that splits a word wherever the size differs at all, and one line
    # was measured to mix 12.0 and 11.999999999999972 (floating-point noise). A word's size is
    # taken from its characters instead
    words = []
    for word in page.extract_words(x_tolerance_ratio=WORD_GAP_RATIO, return_chars=True):
        if any(_center_inside(word, box) for box in table_boxes):   # words inside a table are already in the table
            continue
        sizes = Counter(round(char["size"], 1) for char in word["chars"])
        words.append({**word, "size": sizes.most_common(1)[0][0]})
    return _group_lines(words, page.page_number), tables


def _center_inside(word: dict, box: tuple[float, float, float, float]) -> bool:
    x = (word["x0"] + word["x1"]) / 2
    y = (word["top"] + word["bottom"]) / 2
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


def _group_lines(words: list[dict], page: int) -> list[_Line]:
    """Group words into lines by vertical position.

    pdfplumber's own line grouping compares only the top coordinate, so inline code (smaller
    font, sitting lower) becomes a line of its own, sorted before the next line. Here a word
    joins the line it overlaps vertically the most.
    """
    rows: list[list[dict]] = []
    for word in sorted(words, key=lambda w: (w["top"], w["x0"])):
        for row in reversed(rows[-3:]):   # sorted by top, so only the last few lines can overlap
            anchor = max(row, key=lambda w: w["size"])   # the largest word stands for the line's position
            overlap = min(anchor["bottom"], word["bottom"]) - max(anchor["top"], word["top"])
            shorter = min(anchor["bottom"] - anchor["top"], word["bottom"] - word["top"])
            if shorter > 0 and overlap > SAME_ROW_OVERLAP * shorter:
                row.append(word)
                break
        else:
            rows.append([word])

    lines = []
    for row in rows:
        row.sort(key=lambda w: w["x0"])
        anchor = max(row, key=lambda w: w["size"])
        sizes = Counter()
        for word in row:
            sizes[word["size"]] += len(word["text"])
        lines.append(_Line(text=_join_words(w["text"] for w in row), x0=row[0]["x0"],
                           top=anchor["top"], bottom=anchor["bottom"],
                           size=sizes.most_common(1)[0][0], page=page))
    lines.sort(key=lambda line: line.top)
    return lines


_SPACE_AFTER_OPENING = re.compile(r"([(\[]) ")
_SPACE_BEFORE_CLOSING = re.compile(r" ([)\],.:;!?])")


def _join_words(words) -> str:
    """Join one line's words with spaces. Inline code is set in another font with extra spacing
    around it, so "(" and "campaigns.profit" come out as separate words; English never puts a
    space after an opening bracket or before closing punctuation, so those spaces are dropped."""
    text = " ".join(words)
    return _SPACE_BEFORE_CLOSING.sub(r"\1", _SPACE_AFTER_OPENING.sub(r"\1", text))


class _Assembler:
    """Decides line by line: start a new element, or continue the current one."""

    def __init__(self, lines: list[_Line]):
        sizes = Counter()
        for line in lines:
            sizes[line.size] += len(line.text)
        self.body_size = sizes.most_common(1)[0][0] if sizes else 0.0
        heading_sizes = sorted({s for s in sizes if s >= self.body_size * HEADING_SIZE_RATIO}, reverse=True)
        self.heading_level = {size: i + 1 for i, size in enumerate(heading_sizes)}
        self.list_indents = _cluster(sorted(line.x0 for line in lines if LIST_MARKER.match(line.text)))

        self.elements: list[Element] = []
        self.current: dict | None = None   # the element being assembled: kind, level, ordered, x0, line texts, last line

    def run(self, pages: list[tuple[list[_Line], list[_Table]]]) -> list[Element]:
        for page_lines, page_tables in pages:
            blocks = sorted([*page_lines, *page_tables], key=lambda block: block.top)
            for block in blocks:
                if isinstance(block, _Table):
                    self._flush()
                    self._add_table(block)
                else:
                    self._add_line(block)
        self._flush()
        return self.elements

    def _add_line(self, line: _Line) -> None:
        current = self.current
        level = self.heading_level.get(line.size)
        if level is not None:
            if current and current["kind"] is ElementKind.HEADING and current["level"] == level \
                    and self._close(current["last"], line):
                self._append(line, line.text)   # a heading wrapped onto two lines
            else:
                self._start(ElementKind.HEADING, line, line.text, level=level)
            return

        marker = LIST_MARKER.match(line.text)
        if marker:
            level = 1 + next(i for i, x in enumerate(self.list_indents) if abs(line.x0 - x) <= INDENT_TOLERANCE)
            self._start(ElementKind.LIST_ITEM, line, line.text[marker.end():], level=level,
                        ordered=marker.group("number") is not None)
            return

        if current and current["kind"] is ElementKind.LIST_ITEM \
                and line.x0 >= current["x0"] - INDENT_TOLERANCE and self._close(current["last"], line):
            self._append(line, line.text)   # a wrapped list item: the continuation lines up with the marker or further right
        elif current and current["kind"] is ElementKind.PARAGRAPH and self._close(current["last"], line):
            self._append(line, line.text)
        else:
            self._start(ElementKind.PARAGRAPH, line, line.text)

    def _close(self, previous: _Line, line: _Line) -> bool:
        """Whether line directly continues previous (same paragraph)."""
        if line.page == previous.page:
            return line.top - previous.bottom < PARAGRAPH_GAP_RATIO * previous.size
        # Across a page break there is no gap to compare: if the last line of the previous page
        # does not end a sentence, the paragraph continues
        return line.page == previous.page + 1 and not previous.text.endswith(SENTENCE_END)

    def _start(self, kind: ElementKind, line: _Line, text: str, level: int = 0, ordered: bool = False) -> None:
        self._flush()
        self.current = {"kind": kind, "level": level, "ordered": ordered, "x0": line.x0,
                        "page": line.page, "texts": [text], "last": line}

    def _append(self, line: _Line, text: str) -> None:
        self.current["texts"].append(text)
        self.current["last"] = line

    def _flush(self) -> None:
        current, self.current = self.current, None
        if current is None:
            return
        text = join_lines(current["texts"])
        if text:
            self.elements.append(Element(kind=current["kind"], text=text, level=current["level"],
                                         ordered=current["ordered"], page=current["page"]))

    def _add_table(self, table: _Table) -> None:
        """A table that spills onto the next page shows up there as another table with the same
        number of columns, often with the header repeated (Word / WPS do this); join them back."""
        previous = self.elements[-1] if self.elements else None
        if previous and previous.kind is ElementKind.TABLE and previous.page == table.page - 1 \
                and len(previous.rows[0]) == len(table.rows[0]):
            rows = table.rows[1:] if table.rows[0] == previous.rows[0] else table.rows
            self.elements[-1] = table_element([*previous.rows, *rows], page=previous.page)
        else:
            self.elements.append(table_element(table.rows, page=table.page))


def _cluster(values: list[float]) -> list[float]:
    """Sorted x coordinates; values within the tolerance form one group, returns each group's
    first value. Used to turn list indents into levels."""
    groups: list[float] = []
    for value in values:
        if not groups or value - groups[-1] > INDENT_TOLERANCE:
            groups.append(value)
    return groups
