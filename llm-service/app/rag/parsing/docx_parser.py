"""Word (.docx) -> elements, reading the document structure with python-docx.

A docx is structured already (a zip of XML files): headings are paragraphs with a "Heading 1 /
Heading 2" style, list items are paragraphs with numbering properties, tables are tables.
Nothing to guess, just read it. Word and WPS both save this format.

Only .docx; the old .doc is a different binary format and must be saved as docx first.
Known limitation: a "heading" made by manually bolding and enlarging text, without a heading
style, is read as an ordinary paragraph.
"""
import re
from io import BytesIO

from docx import Document
from docx.document import Document as DocxDocument
from docx.oxml.text.parfmt import CT_PPr
from docx.styles.style import BaseStyle
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.rag.parsing.elements import (
    DocumentParseError,
    Element,
    ElementKind,
    ParsedDocument,
    clean_text,
    finalize,
    table_element,
)

# A built-in heading style is always named "heading 1" inside the file (in every UI language),
# but its style ID is not fixed (WPS saves "2", "3"), so match on the name, not the ID
HEADING_STYLE = re.compile(r"heading ([1-9])", re.IGNORECASE)


class DocxParser:
    def parse(self, data: bytes) -> ParsedDocument:
        try:
            document = Document(BytesIO(data))
        except Exception as e:   # not a zip, missing parts, broken XML... all mean "can't open the file"
            raise DocumentParseError(f"cannot open the docx file: {e}") from e

        elements: list[Element] = []
        # iter_inner_content yields paragraphs and tables in document order; iterating
        # paragraphs and tables separately would lose which paragraphs a table sits between
        for block in document.iter_inner_content():
            if isinstance(block, Table):
                rows = _table_rows(block)
                if rows:
                    elements.append(table_element(rows))
            else:
                element = _paragraph_element(block, document)
                if element is not None:
                    elements.append(element)
        return finalize(elements)


def _paragraph_element(paragraph: Paragraph, document: DocxDocument) -> Element | None:
    text = clean_text(paragraph.text)
    if not text:
        return None
    level = _heading_level(paragraph)
    if level is not None:
        return Element(kind=ElementKind.HEADING, text=text, level=level)
    numbering = _numbering(paragraph)
    if numbering is not None:
        num_id, ilvl = numbering
        return Element(kind=ElementKind.LIST_ITEM, text=text, level=ilvl + 1,
                       ordered=_is_ordered(document, num_id, ilvl))
    return Element(kind=ElementKind.PARAGRAPH, text=text)


def _style_chain(style: BaseStyle | None):
    """A paragraph style and the styles it inherits from. Custom styles (say, "Company Heading")
    are often based on "Heading 1"."""
    while style is not None:
        yield style
        style = style.base_style


def _heading_level(paragraph: Paragraph) -> int | None:
    for style in _style_chain(paragraph.style):
        if style.name == "Title":   # Word's "Title" style ranks above "Heading 1"; finalize re-ranks the levels
            return 0
        match = HEADING_STYLE.fullmatch(style.name or "")
        if match:
            return int(match.group(1))
    return None


def _numbering(paragraph: Paragraph) -> tuple[int, int] | None:
    """(numbering definition ID, level), or None if the paragraph is not a list item.

    Numbering can sit on the paragraph itself or come from its style (such as the built-in
    "List Number" style); the paragraph's own setting wins.
    The "1." "2." a list item shows is not in the paragraph text: Word draws it from the
    numbering definition, so text never contains the number.
    """
    ilvl = None
    sources: list[CT_PPr | None] = [paragraph._p.pPr]
    sources += [style.element.pPr for style in _style_chain(paragraph.style)]
    for ppr in sources:
        if ppr is None or ppr.numPr is None:
            continue
        if ilvl is None and ppr.numPr.ilvl is not None:
            ilvl = ppr.numPr.ilvl.val
        if ppr.numPr.numId is not None:
            num_id = ppr.numPr.numId.val
            return None if num_id == 0 else (num_id, ilvl or 0)   # numId 0 means "numbering removed"
    return None


def _is_ordered(document: DocxDocument, num_id: int, ilvl: int) -> bool:
    """Look up this level's format in the numbering definition: bullet means bullets; decimal,
    roman numerals, letters and so on count as numbered. lvlOverride (a few documents override
    one level's format inside num) is not handled; add it when a document needs it."""
    numbering = document.part.numbering_part.element
    abstract_ids = numbering.xpath(f'./w:num[@w:numId="{num_id}"]/w:abstractNumId/@w:val')
    if not abstract_ids:
        return False
    formats = numbering.xpath(
        f'./w:abstractNum[@w:abstractNumId="{abstract_ids[0]}"]/w:lvl[@w:ilvl="{ilvl}"]/w:numFmt/@w:val')
    return bool(formats) and formats[0] not in ("bullet", "none")


def _table_rows(table: Table) -> list[tuple[str, ...]]:
    rows = []
    for row in table.rows:
        cells = []
        previous = None
        for cell in row.cells:
            if cell._tc is previous:   # python-docx returns a horizontally merged cell once per grid column it spans
                continue
            previous = cell._tc
            cells.append(clean_text(cell.text))
        if any(cells):
            rows.append(tuple(cells))
    return rows
