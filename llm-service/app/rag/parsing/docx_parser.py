"""Word(.docx)-> 元素。用 python-docx 读文档结构。

docx 本身就是结构化的(一个 zip 包,里面是 XML):标题是"标题 1 / 标题 2"样式,列表是带编号属性的
段落,表格就是表格——不用猜,直接读。Word 和 WPS 存出来的 docx 都是这个格式。

只支持 .docx;老的 .doc 是另一种二进制格式,要先另存为 docx。
已知限制:没用标题样式、只是手动加粗加大字号的"标题",会被当成普通段落。
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

# 内置标题样式在文件里的名字永远是英文 "heading 1"(界面上显示成"标题 1"),
# 样式 ID 却不固定(实测 WPS 存的是 "2"、"3"),所以按名字认,不按 ID 认
HEADING_STYLE = re.compile(r"heading ([1-9])", re.IGNORECASE)


class DocxParser:
    def parse(self, data: bytes) -> ParsedDocument:
        try:
            document = Document(BytesIO(data))
        except Exception as e:   # 不是 zip、zip 里缺文件、XML 坏了……都是"文件打不开"
            raise DocumentParseError(f"无法打开 docx 文件:{e}") from e

        elements: list[Element] = []
        # iter_inner_content 按文档里的先后顺序给出段落和表格;
        # 分别遍历 paragraphs 和 tables 会丢掉"表格在哪两段之间"的信息
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
    """段落样式和它继承的上层样式。自定义样式(比如"公司标题")常常是基于"标题 1"改的。"""
    while style is not None:
        yield style
        style = style.base_style


def _heading_level(paragraph: Paragraph) -> int | None:
    for style in _style_chain(paragraph.style):
        if style.name == "Title":   # Word 的"标题"样式,比"标题 1"还高一级;finalize 会重新编号
            return 0
        match = HEADING_STYLE.fullmatch(style.name or "")
        if match:
            return int(match.group(1))
    return None


def _numbering(paragraph: Paragraph) -> tuple[int, int] | None:
    """(编号定义 ID, 层级)。不是列表项返回 None。

    编号可以直接写在段落上,也可以来自段落样式(比如内置的"列表编号"样式);段落上的优先。
    列表项显示的 "1." "2." 不在段落文字里,是 Word 按编号定义现画的,所以 text 里没有编号。
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
            return None if num_id == 0 else (num_id, ilvl or 0)   # numId 0 表示"取消编号"
    return None


def _is_ordered(document: DocxDocument, num_id: int, ilvl: int) -> bool:
    """查编号定义里这一层的格式:bullet 是项目符号,decimal / 中文数字等都算编号。
    没处理 lvlOverride(个别文档会在 num 里覆盖某一层的格式),遇到了再加。"""
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
            if cell._tc is previous:   # 横向合并的格子,python-docx 会把同一个格子返回多次
                continue
            previous = cell._tc
            cells.append(clean_text(cell.text))
        if any(cells):
            rows.append(tuple(cells))
    return rows
