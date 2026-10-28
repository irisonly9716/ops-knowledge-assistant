"""解析层的统一输出:不管原文件是 Markdown、docx 还是 PDF,都变成同一种"带类型的元素"序列。

为什么按元素类型解析,而不是直接抽成一整段纯文本:
  - 切块要按标题、按 SOP 步骤切,得先知道哪一行是标题、哪几行属于同一个列表项
  - 每个块要带上它所在的标题路径(比如 风控问题处理流程 > 处理步骤),检索和引用都用得上
  - 表格要保留成行和列,拆成一行行文字就看不出哪个数属于哪一列

后面的切块只依赖这里的类型,不关心原文件是什么格式。
"""
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, Protocol


class ElementKind(StrEnum):
    HEADING = "heading"       # 标题
    PARAGRAPH = "paragraph"   # 正文段落
    LIST_ITEM = "list_item"   # 列表项(SOP 的步骤大多是编号列表)
    TABLE = "table"           # 表格


@dataclass(frozen=True)
class Element:
    kind: ElementKind
    text: str
    # heading:标题级别,1 是最高一级;list_item:嵌套层级,1 是最外层;其它类型为 0
    level: int = 0
    ordered: bool = False                    # list_item:编号列表为 True,项目符号为 False
    rows: tuple[tuple[str, ...], ...] = ()   # table:每行每格的文字,第一行是表头
    page: int | None = None                  # 所在页码,只有 PDF 有
    heading_path: tuple[str, ...] = ()       # 所在位置的各级标题,从外到内;标题自己不算在内


@dataclass(frozen=True)
class ParsedDocument:
    elements: tuple[Element, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)  # Markdown 的 frontmatter;其它格式为空


class DocumentParseError(Exception):
    """文件本身有问题:格式不支持、文件损坏、编码不对、PDF 没有文字层……
    同一个文件重试多少次结果都一样,接进消费者时按永久错误处理。"""


class DocumentParser(Protocol):
    """输入是文件的字节,不是路径:文件以后可能在 S3 上,解析器不该关心它从哪来。"""

    def parse(self, data: bytes) -> ParsedDocument: ...


def finalize(elements: list[Element], metadata: Mapping[str, Any] | None = None) -> ParsedDocument:
    """三种解析器共用的最后一步:统一标题级别,算出每个元素的标题路径。

    标题级别按"相对排名"重新编号:文档里出现过的级别从高到低依次记为 1、2、3。
    只用二级、三级标题的 Markdown,和同样结构、但 PDF 里只能看出"大字号 / 小字号"的文件,
    这样会得到同样的级别;Word 的"标题"样式(比"标题 1"还高一级)也能自然地排进来。
    """
    if not elements:
        raise DocumentParseError("没有解析出任何内容")
    levels = sorted({e.level for e in elements if e.kind is ElementKind.HEADING})
    rank = {level: i + 1 for i, level in enumerate(levels)}

    path: list[tuple[int, str]] = []   # 当前位置的各级标题:(级别, 文字),从外到内
    result = []
    for element in elements:
        if element.kind is ElementKind.HEADING:
            level = rank[element.level]
            while path and path[-1][0] >= level:   # 遇到同级或更高级的标题,退出之前的小节
                path.pop()
            result.append(replace(element, level=level, heading_path=tuple(text for _, text in path)))
            path.append((level, element.text))
        else:
            result.append(replace(element, heading_path=tuple(text for _, text in path)))
    return ParsedDocument(elements=tuple(result), metadata=dict(metadata or {}))


def table_element(rows: list[tuple[str, ...]], page: int | None = None) -> Element:
    # text 是给人看、给后面 embedding 用的一份文字版;结构以 rows 为准
    text = "\n".join(" | ".join(row) for row in rows)
    return Element(kind=ElementKind.TABLE, text=text, rows=tuple(rows), page=page)


# 中日韩文字和全角标点。折行时两边只要有一个是这类字符,就不该补空格
_CJK = re.compile(r"[　-〿㐀-䶿一-鿿＀-￯]")
_SPACES = re.compile(r"[^\S\n]+")   # 除换行以外的连续空白


def is_cjk(char: str) -> bool:
    return bool(_CJK.match(char))


def join_lines(lines: Iterable[str]) -> str:
    """把折成多行的一段文字拼回一行。

    中文折行处本来就没有空格,直接拼;两边都是英文或数字时要补一个空格,否则
    "system" + "bug" 会粘成 "systembug"。Markdown 原文里的手动折行、PDF 的自动折行都走这里。
    """
    text = ""
    for line in lines:
        line = _SPACES.sub(" ", line).strip()
        if not line:
            continue
        if text and not is_cjk(text[-1]) and not is_cjk(line[0]):
            text += " "
        text += line
    return text


def clean_text(text: str) -> str:
    """去掉首尾空白,合并连续空白,段内换行按 join_lines 的规则拼起来。"""
    return join_lines(text.splitlines())
