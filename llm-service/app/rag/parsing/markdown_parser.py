"""Markdown -> 元素。

用 markdown-it-py(按 CommonMark 标准实现的解析器)先把文本变成 token 流,再按 token 类型映射成元素。
不自己用正则认 "# 标题"、"1. 列表":代码块里的 #、嵌套列表的缩进、表格里的 |,这些边界情况
标准解析器都已经处理好了。
"""
import re

import yaml
from markdown_it import MarkdownIt
from markdown_it.token import Token

from app.rag.parsing.elements import (
    DocumentParseError,
    Element,
    ElementKind,
    ParsedDocument,
    finalize,
    join_lines,
    table_element,
)

# 文件开头用 --- 包起来的一段 YAML(见 sop_docs/README.md 的格式约定)
FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)


class MarkdownParser:
    def __init__(self) -> None:
        # CommonMark 本身没有表格语法,表格是 GitHub 风格的扩展,要单独打开
        self._md = MarkdownIt("commonmark").enable("table")

    def parse(self, data: bytes) -> ParsedDocument:
        try:
            text = data.decode("utf-8-sig")   # -sig:顺带去掉 Windows 记事本可能加的 BOM
        except UnicodeDecodeError as e:
            raise DocumentParseError("Markdown 文件不是 UTF-8 编码,请另存为 UTF-8 后重新上传") from e

        metadata = {}
        match = FRONTMATTER.match(text)
        if match:
            try:
                metadata = yaml.safe_load(match.group(1)) or {}   # safe_load:不执行 YAML 里的任意对象构造
            except yaml.YAMLError as e:
                raise DocumentParseError(f"frontmatter 不是合法的 YAML:{e}") from e
            if not isinstance(metadata, dict):
                raise DocumentParseError("frontmatter 应该是 key: value 的形式")
            text = text[match.end():]

        return finalize(_elements(self._md.parse(text)), metadata)


def _elements(tokens: list[Token]) -> list[Element]:
    elements: list[Element] = []
    lists: list[bool] = []          # 当前所在的各层列表是不是编号列表;长度就是嵌套层级
    item_has_text: list[bool] = []  # 当前各层列表项是否已经产出过文字
    heading_level: int | None = None
    table_rows: list[tuple[str, ...]] | None = None
    row: list[str] = []

    for token in tokens:
        match token.type:
            case "heading_open":
                heading_level = int(token.tag[1])   # "h2" -> 2
            case "bullet_list_open" | "ordered_list_open":
                lists.append(token.type == "ordered_list_open")
            case "bullet_list_close" | "ordered_list_close":
                lists.pop()
            case "list_item_open":
                item_has_text.append(False)
            case "list_item_close":
                item_has_text.pop()
            case "table_open":
                table_rows = []
            case "tr_open":
                row = []
            case "tr_close":
                table_rows.append(tuple(row))
            case "table_close":
                elements.append(table_element(table_rows))
                table_rows = None
            case "fence" | "code_block":
                if token.content.strip():
                    elements.append(Element(kind=ElementKind.PARAGRAPH, text=token.content.strip()))
            case "inline":
                text = _inline_text(token)
                if table_rows is not None:
                    row.append(text)                 # 表格里的每个 inline 就是一个格子,空格子也要占位
                elif not text:
                    continue
                elif heading_level is not None:
                    elements.append(Element(kind=ElementKind.HEADING, text=text, level=heading_level))
                    heading_level = None
                elif item_has_text and not item_has_text[-1]:
                    elements.append(Element(kind=ElementKind.LIST_ITEM, text=text,
                                            level=len(lists), ordered=lists[-1]))
                    item_has_text[-1] = True
                else:
                    # 普通段落;列表项里第二段以后的文字也算段落
                    elements.append(Element(kind=ElementKind.PARAGRAPH, text=text))
    return elements


def _inline_text(token: Token) -> str:
    """一行 inline 内容的纯文字:去掉加粗、链接这些标记,保留文字和行内代码。"""
    parts = []
    for child in token.children or []:
        if child.type in ("text", "code_inline"):
            parts.append(child.content)
        elif child.type in ("softbreak", "hardbreak"):
            parts.append("\n")
        elif child.type == "image":
            parts.append(child.content)   # 图片只留替代文字
    return join_lines("".join(parts).split("\n"))
