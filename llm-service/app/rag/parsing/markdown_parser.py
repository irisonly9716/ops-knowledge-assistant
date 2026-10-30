"""Markdown -> elements.

markdown-it-py (a parser that implements the CommonMark spec) first turns the text into a token
stream, and each token type maps to an element. No hand-written regexes for "# heading" or
"1. list": a # inside a code block, the indentation of nested lists, a | inside a table are edge
cases the standard parser already handles.
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

# A YAML block between --- lines at the top of the file (format described in sop_docs/README.md)
FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)


class MarkdownParser:
    def __init__(self) -> None:
        # CommonMark itself has no table syntax; tables are a GitHub extension and must be enabled
        self._md = MarkdownIt("commonmark").enable("table")

    def parse(self, data: bytes) -> ParsedDocument:
        try:
            text = data.decode("utf-8-sig")   # -sig: also drops the BOM Windows Notepad may add
        except UnicodeDecodeError as e:
            raise DocumentParseError("the Markdown file is not UTF-8; save it as UTF-8 and upload it again") from e

        metadata = {}
        match = FRONTMATTER.match(text)
        if match:
            try:
                metadata = yaml.safe_load(match.group(1)) or {}   # safe_load: never constructs arbitrary objects from YAML
            except yaml.YAMLError as e:
                raise DocumentParseError(f"the frontmatter is not valid YAML: {e}") from e
            if not isinstance(metadata, dict):
                raise DocumentParseError("the frontmatter must be key: value pairs")
            text = text[match.end():]

        return finalize(_elements(self._md.parse(text)), metadata)


def _elements(tokens: list[Token]) -> list[Element]:
    elements: list[Element] = []
    lists: list[bool] = []          # whether each enclosing list is numbered; its length is the nesting level
    item_has_text: list[bool] = []  # whether each enclosing list item has produced its text yet
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
                    row.append(text)                 # inside a table every inline is one cell; empty cells keep their place
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
                    # an ordinary paragraph; a list item's second and later paragraphs are paragraphs too
                    elements.append(Element(kind=ElementKind.PARAGRAPH, text=text))
    return elements


def _inline_text(token: Token) -> str:
    """The plain text of one inline token: drops bold, link and similar markup, keeps text and inline code."""
    parts = []
    for child in token.children or []:
        if child.type in ("text", "code_inline"):
            parts.append(child.content)
        elif child.type in ("softbreak", "hardbreak"):
            parts.append("\n")
        elif child.type == "image":
            parts.append(child.content)   # images keep only their alt text
    return join_lines("".join(parts).split("\n"))
