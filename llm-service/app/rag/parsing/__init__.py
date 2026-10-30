"""Document parsing: Markdown / docx / PDF -> the same typed elements (see elements.py).

Callers use only parse_file or parser_for and never depend on a parsing library directly. To use
another implementation later (say, Amazon Textract for scans), write one more DocumentParser and
register it in PARSERS.
"""
from pathlib import Path

from app.rag.parsing.docx_parser import DocxParser
from app.rag.parsing.elements import (
    DocumentParseError,
    DocumentParser,
    Element,
    ElementKind,
    ParsedDocument,
)
from app.rag.parsing.markdown_parser import MarkdownParser
from app.rag.parsing.pdf_parser import PdfParser

# Parser by file extension. Stored file names keep the original extension (see Java's sanitizeFilename)
PARSERS: dict[str, DocumentParser] = {
    ".md": MarkdownParser(),
    ".markdown": MarkdownParser(),
    ".docx": DocxParser(),
    ".pdf": PdfParser(),
}


class UnsupportedFormatError(DocumentParseError):
    pass


def parser_for(filename: str) -> DocumentParser:
    suffix = Path(filename).suffix.lower()
    try:
        return PARSERS[suffix]
    except KeyError:
        supported = ", ".join(sorted(PARSERS))
        raise UnsupportedFormatError(
            f"unsupported file type {suffix or '(no extension)'}; supported: {supported}. "
            "Save Word .doc files as .docx first") from None


def parse_file(path: Path) -> ParsedDocument:
    return parser_for(path.name).parse(path.read_bytes())


__all__ = [
    "DocumentParseError", "DocumentParser", "Element", "ElementKind", "ParsedDocument",
    "UnsupportedFormatError", "parse_file", "parser_for",
]
