"""文档解析:Markdown / docx / PDF -> 统一的"带类型的元素"(见 elements.py)。

调用方只用 parse_file 或 parser_for,不直接依赖某个解析库。以后要换成别的实现
(比如扫描件走 Amazon Textract),只需要多写一个 DocumentParser、在 PARSERS 里登记。
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

# 按扩展名选解析器。上传时存下来的文件名保留了原始扩展名(见 Java 的 sanitizeFilename)
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
        supported = "、".join(sorted(PARSERS))
        raise UnsupportedFormatError(
            f"不支持的文件类型 {suffix or '(没有扩展名)'};目前支持 {supported}。"
            "Word 的 .doc 请先另存为 .docx") from None


def parse_file(path: Path) -> ParsedDocument:
    return parser_for(path.name).parse(path.read_bytes())


__all__ = [
    "DocumentParseError", "DocumentParser", "Element", "ElementKind", "ParsedDocument",
    "UnsupportedFormatError", "parse_file", "parser_for",
]
