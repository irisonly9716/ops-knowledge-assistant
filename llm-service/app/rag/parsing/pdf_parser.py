"""PDF -> 元素。用 pdfplumber 取出每个字的位置和字号,再推断结构。

只支持有文字层的"数字版"PDF(Word / WPS 导出的那种)。扫描件里只有图片,要 OCR,暂不支持。

PDF 跟 docx 最大的区别:PDF 里没有"标题""段落""列表",只有"在第几页的什么坐标、用多大的字画了
哪个字"。所以结构全靠推断(规则按 Word / WPS 的常见排版定):
  行      纵向位置重叠的字算同一行(不能只看顶部坐标,行内代码字号小、位置偏低)
  标题    字号明显大于正文;字号越大级别越高
  列表项  行首是编号(1.  2、  3))或项目符号;按编号的横向位置分层,越靠右层级越深
  段落    跟上一行挨得近、又不是标题或列表开头的,接在上一段后面
  表格    pdfplumber 按表格线找;跨页的表格拼回一张;没有边框线的表格认不出来,会当成普通文字
单栏、从上往下读;双栏排版、页眉页脚、首行缩进区分段落,目前都不处理。
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
    is_cjk,
    join_lines,
    table_element,
)

LIST_MARKER = re.compile(
    r"^(?:(?P<number>\d{1,3})(?:\.(?!\d)|[、)）])"   # 1.  2、  3)  —— "1.23 万"这种小数不算
    r"|[•●○◦▪■□]"                             # 项目符号; 是 Word / WPS 用 Symbol 字体画的圆点
    r"|[-*](?=\s))\s*"
)
HEADING_SIZE_RATIO = 1.2        # 字号至少是正文的 1.2 倍才算标题
SAME_ROW_OVERLAP = 0.4          # 纵向重叠超过较矮那个字高的 40%,算同一行
PARAGRAPH_GAP_RATIO = 0.6       # 跟上一行的空隙超过 0.6 个字高,算新的一段
INDENT_TOLERANCE = 3.0          # 横向位置相差 3pt 以内算对齐(单位是 pt,1/72 英寸)
SENTENCE_END = tuple("。！？!?.")


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
        except Exception as e:   # 不是 PDF、文件损坏、加了密码……
            raise DocumentParseError(f"无法读取 PDF:{e}") from e

        lines = [line for page_lines, _ in pages for line in page_lines]
        tables = [table for _, page_tables in pages for table in page_tables]
        if not lines and not tables:
            raise DocumentParseError("PDF 里没有文字层(可能是扫描件或图片),需要 OCR,目前不支持")
        return finalize(_Assembler(lines).run(pages))


def _read_page(page) -> tuple[list[_Line], list[_Table]]:
    tables = []
    table_boxes = []
    for found in page.find_tables():
        rows = [tuple(clean_text(cell or "") for cell in row) for row in found.extract()]
        # 只有一行或一列的"表格"多半是加了边框的文本框,当普通文字处理
        if len(rows) >= 2 and len(rows[0]) >= 2:
            tables.append(_Table(rows=rows, top=found.bbox[1], page=page.page_number))
            table_boxes.append(found.bbox)

    # 不用 extra_attrs=["size"]:那样字号只要有一点不同就会拆成两个词,实测同一行里有 12.0 和
    # 11.999999999999972 两种"字号"(浮点误差),拼回来就多出空格。字号改成从每个词的字里取
    words = []
    for word in page.extract_words(return_chars=True):
        if any(_center_inside(word, box) for box in table_boxes):   # 表格里的字已经在表格里了
            continue
        sizes = Counter(round(char["size"], 1) for char in word["chars"])
        words.append({**word, "size": sizes.most_common(1)[0][0]})
    return _group_lines(words, page.page_number), tables


def _center_inside(word: dict, box: tuple[float, float, float, float]) -> bool:
    x = (word["x0"] + word["x1"]) / 2
    y = (word["top"] + word["bottom"]) / 2
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


def _group_lines(words: list[dict], page: int) -> list[_Line]:
    """把词按纵向位置归到行里。

    pdfplumber 自带的分行只比较字的顶部坐标,行内代码(字号小、位置偏低)会被分成单独一行,
    还排到下一行前面去。这里改成看纵向重叠:跟哪一行重叠得多就归哪一行。
    """
    rows: list[list[dict]] = []
    for word in sorted(words, key=lambda w: (w["top"], w["x0"])):
        for row in reversed(rows[-3:]):   # 按顶部排过序,能重叠的只可能是最近的几行
            anchor = max(row, key=lambda w: w["size"])   # 用这一行里最大的字代表这一行的位置
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


def _join_words(words) -> str:
    """同一行里的词:两边都是汉字时直接拼(中间的空隙是排版造成的),否则用空格隔开。"""
    text = ""
    for word in words:
        if text and not (is_cjk(text[-1]) and is_cjk(word[0])):
            text += " "
        text += word
    return text


class _Assembler:
    """逐行决定:新开一个元素,还是接在当前元素后面。"""

    def __init__(self, lines: list[_Line]):
        sizes = Counter()
        for line in lines:
            sizes[line.size] += len(line.text)
        self.body_size = sizes.most_common(1)[0][0] if sizes else 0.0
        heading_sizes = sorted({s for s in sizes if s >= self.body_size * HEADING_SIZE_RATIO}, reverse=True)
        self.heading_level = {size: i + 1 for i, size in enumerate(heading_sizes)}
        self.list_indents = _cluster(sorted(line.x0 for line in lines if LIST_MARKER.match(line.text)))

        self.elements: list[Element] = []
        self.current: dict | None = None   # 正在拼的元素:kind、level、ordered、x0、各行文字、最后一行

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
                self._append(line, line.text)   # 一个标题折成了两行
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
            self._append(line, line.text)   # 列表项折行:折下来的行跟编号对齐或更靠右
        elif current and current["kind"] is ElementKind.PARAGRAPH and self._close(current["last"], line):
            self._append(line, line.text)
        else:
            self._start(ElementKind.PARAGRAPH, line, line.text)

    def _close(self, previous: _Line, line: _Line) -> bool:
        """line 是不是紧接着 previous 的下一行(同一段)。"""
        if line.page == previous.page:
            return line.top - previous.bottom < PARAGRAPH_GAP_RATIO * previous.size
        # 跨页了,没法比间距:上一页最后一行没有以句号结尾,就当成这一段还没完
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
        """表格跨页时,下一页开头会接着出现一张同样列数的表(Word / WPS 常常还会重复表头),拼回一张。"""
        previous = self.elements[-1] if self.elements else None
        if previous and previous.kind is ElementKind.TABLE and previous.page == table.page - 1 \
                and len(previous.rows[0]) == len(table.rows[0]):
            rows = table.rows[1:] if table.rows[0] == previous.rows[0] else table.rows
            self.elements[-1] = table_element([*previous.rows, *rows], page=previous.page)
        else:
            self.elements.append(table_element(table.rows, page=table.page))


def _cluster(values: list[float]) -> list[float]:
    """排好序的横坐标,相差在容差以内的归为一组,返回每组的第一个值。用来给列表缩进分层。"""
    groups: list[float] = []
    for value in values:
        if not groups or value - groups[-1] > INDENT_TOLERANCE:
            groups.append(value)
    return groups
