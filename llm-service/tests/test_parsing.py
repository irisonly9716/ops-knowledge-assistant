"""解析层的测试。只读本地文件,不联网。

最重要的是第一组:同一篇文档的 Markdown / docx / PDF 三个版本,解析出同样的结构。
docx 和 PDF 在 tests/fixtures/documents/ 里,由 scripts/make_sample_documents.py 从 Markdown 生成。
"""
import re
from io import BytesIO
from pathlib import Path

import docx
import pytest
from docx.enum.style import WD_STYLE_TYPE

from app.rag.parsing import (
    DocumentParseError,
    ElementKind,
    UnsupportedFormatError,
    parse_file,
    parser_for,
)
from app.rag.parsing.docx_parser import DocxParser
from app.rag.parsing.markdown_parser import MarkdownParser
from app.rag.parsing.pdf_parser import PdfParser, _Assembler, _Line

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "documents"

SAMPLES = [
    REPO_ROOT / "sop_docs" / "风控问题处理流程.md",           # 两级列表
    REPO_ROOT / "sop_docs" / "主播签约考核流程-3C数码.md",
    REPO_ROOT / "campaign_docs" / "美妆618大促-复盘.md",      # 表格,其中一张跨页
]


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _structure(document) -> list[tuple]:
    """比较用的结构:类型、级别、编号与否、文字、标题路径、表格内容。

    文字去掉所有空白再比:PDF 里没有可靠的空格字符,空格是按字和字之间的距离推出来的
    ("美妆618大促" 在 PDF 里会读成 "美妆 618 大促");空格不同不算结构不同。
    嵌套列表不比较"是不是编号":生成样例时 WPS 把编号列表里嵌套的项目符号改成了编号,
    这是转换工具改了内容,不是解析器的问题。
    """
    return [
        (e.kind, e.level,
         e.ordered if e.kind is not ElementKind.LIST_ITEM or e.level == 1 else None,
         _squash(e.text),
         tuple(_squash(title) for title in e.heading_path),
         tuple(tuple(_squash(cell) for cell in row) for row in e.rows))
        for e in document.elements
    ]


# ---------------------------------------------------------------------------
# 三种格式结果一致
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("suffix", [".docx", ".pdf"])
@pytest.mark.parametrize("markdown", SAMPLES, ids=lambda path: path.stem)
def test_same_structure_as_markdown(markdown: Path, suffix: str):
    expected = parse_file(markdown)
    actual = parse_file(FIXTURES / f"{markdown.stem}{suffix}")
    assert _structure(actual) == _structure(expected)


def test_fixtures_exist_for_every_sample():
    # 少了样例文件时上面的参数化测试会报 FileNotFoundError,这里给一个更直接的提示
    missing = [f"{md.stem}{suffix}" for md in SAMPLES for suffix in (".docx", ".pdf")
               if not (FIXTURES / f"{md.stem}{suffix}").is_file()]
    assert not missing, f"缺少样例,运行 scripts/make_sample_documents.py 重新生成:{missing}"


def test_heading_levels_and_paths():
    document = parse_file(SAMPLES[0])
    headings = [(e.level, e.text, e.heading_path) for e in document.elements if e.kind is ElementKind.HEADING]
    assert headings == [
        (1, "风控问题处理流程", ()),
        (2, "适用场景", ("风控问题处理流程",)),
        (2, "处理步骤", ("风控问题处理流程",)),
        (2, "协同部门", ("风控问题处理流程",)),
    ]
    steps = [e for e in document.elements if e.heading_path == ("风控问题处理流程", "处理步骤")]
    assert [(e.level, e.ordered) for e in steps] == [(1, True)] * 7 + [(2, False)] * 2
    assert steps[0].text.startswith("确认命中的规则和账号")   # 编号 "1." 不在文字里


# ---------------------------------------------------------------------------
# PDF 特有的推断
# ---------------------------------------------------------------------------
def test_pdf_page_numbers():
    pdf = parse_file(FIXTURES / "美妆618大促-复盘.pdf")
    assert all(e.page is not None for e in pdf.elements)
    findings = next(e for e in pdf.elements if e.text.startswith("几个发现"))
    assert findings.page == 2
    assert all(e.page is None for e in parse_file(FIXTURES / "美妆618大促-复盘.docx").elements)


def test_pdf_table_split_across_pages_is_merged():
    tables = [e for e in parse_file(FIXTURES / "美妆618大促-复盘.pdf").elements if e.kind is ElementKind.TABLE]
    products = tables[1]
    assert products.page == 1                            # 表格从第 1 页开始
    assert [row[0] for row in products.rows] == ["商品", "防晒霜", "面膜", "精华液", "口红", "粉底液", "眼影盘"]
    # 第 2 页重复的表头只保留一次;最后两行来自第 2 页


def test_pdf_inline_code_stays_in_its_sentence():
    # 行内代码字号小、位置偏低,如果按顶部坐标分行,它会变成单独的一行、排到别的地方去
    pdf = parse_file(FIXTURES / "美妆618大促-复盘.pdf")
    paragraph = next(e for e in pdf.elements if e.text.startswith("之前两版"))
    assert "商品成本已经在campaign_products.profit里扣过了" in _squash(paragraph.text)


def test_pdf_font_size_rounding_does_not_add_spaces():
    # 这一行里冒号的字号是 11.999999999999972,汉字是 12.0。按字号拆词的话会读成 "风控组 : 第一联系人 , ..."
    pdf = parse_file(FIXTURES / "风控问题处理流程.pdf")
    assert any(e.text == "风控组:第一联系人,处理申诉审核" for e in pdf.elements)


def test_pdf_bullet_glyph_is_not_kept_in_text():
    # Word / WPS 的圆点是 Symbol 字体里的私有区字符 ,留在文字里就是一个乱码
    for markdown in SAMPLES:
        pdf = parse_file(FIXTURES / f"{markdown.stem}.pdf")
        assert not any("" in e.text for e in pdf.elements)


@pytest.mark.parametrize("last_line_on_page_1, second_page, expected", [
    # 上一页最后一行没有句号:这一段还没完,接到下一页
    ("风控申诉材料包括直播回放、商品资质", "和物流单据。", ["风控申诉材料包括直播回放、商品资质和物流单据。"]),
    # 以句号结尾:下一页是新的一段
    ("第一段到这里结束。", "第二段从新的一页开始。", ["第一段到这里结束。", "第二段从新的一页开始。"]),
])
def test_pdf_paragraph_across_page_break(last_line_on_page_1, second_page, expected):
    # 样例里没有跨页的段落,直接用"已经分好行"的数据测这条规则
    page_1 = [_Line(text=last_line_on_page_1, x0=90, top=760, bottom=772, size=12.0, page=1)]
    page_2 = [_Line(text=second_page, x0=90, top=72, bottom=84, size=12.0, page=2)]
    elements = _Assembler(page_1 + page_2).run([(page_1, []), (page_2, [])])
    assert [e.text for e in elements] == expected


def test_pdf_without_text_layer_is_rejected():
    # 一页空白、没有任何文字的 PDF,相当于扫描件(扫描件里只有图片)
    blank = (b"%PDF-1.4\n1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
             b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
             b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] >> endobj\n"
             b"trailer << /Root 1 0 R >>\n%%EOF\n")
    with pytest.raises(DocumentParseError, match="扫描件"):
        PdfParser().parse(blank)


def test_broken_pdf_is_rejected():
    with pytest.raises(DocumentParseError, match="无法读取 PDF"):
        PdfParser().parse(b"this is not a pdf")


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------
def _md(text: str):
    return MarkdownParser().parse(text.encode("utf-8"))


def test_markdown_frontmatter_becomes_metadata_not_text():
    document = parse_file(SAMPLES[0])
    assert document.metadata["department"] == "ALL"
    assert not any("department" in e.text for e in document.elements)


def test_markdown_heading_levels_are_relative():
    # 只用了二级、三级标题,记为 1、2;跟只能分出"大字号 / 小字号"的 PDF 对得上
    document = _md("## 总则\n\n正文一\n\n### 细则\n\n正文二\n\n## 附录\n\n正文三\n")
    assert [(e.kind, e.level, e.heading_path) for e in document.elements] == [
        (ElementKind.HEADING, 1, ()),
        (ElementKind.PARAGRAPH, 0, ("总则",)),
        (ElementKind.HEADING, 2, ("总则",)),
        (ElementKind.PARAGRAPH, 0, ("总则", "细则")),
        (ElementKind.HEADING, 1, ()),                  # 回到一级,退出"总则 > 细则"
        (ElementKind.PARAGRAPH, 0, ("附录",)),
    ]


def test_markdown_line_breaks_inside_a_paragraph():
    document = _md("第一行\n第二行\n\nhello\nworld\n")
    assert [e.text for e in document.elements] == ["第一行第二行", "hello world"]


def test_markdown_table_keeps_empty_cells():
    document = _md("| 指标 | 备注 |\n|---|---|\n| GMV | |\n")
    assert document.elements[0].rows == (("指标", "备注"), ("GMV", ""))


def test_markdown_must_be_utf8():
    with pytest.raises(DocumentParseError, match="UTF-8"):
        MarkdownParser().parse("# 中文标题".encode("gbk"))


def test_markdown_bad_frontmatter_is_rejected():
    with pytest.raises(DocumentParseError, match="YAML"):
        _md("---\ntitle: [没闭合\n---\n正文\n")


def test_empty_document_is_rejected():
    with pytest.raises(DocumentParseError, match="没有解析出任何内容"):
        _md("---\ntitle: 只有 frontmatter\n---\n")


# ---------------------------------------------------------------------------
# docx(用 python-docx 现场生成,不依赖样例文件)
# ---------------------------------------------------------------------------
def _docx(build) -> bytes:
    document = docx.Document()
    build(document)
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_docx_title_and_heading_styles():
    def build(d):
        d.add_heading("员工手册", level=0)       # "标题"样式,比"标题 1"还高一级
        d.add_heading("考勤", level=1)
        d.add_paragraph("每天 9 点打卡。")
    document = DocxParser().parse(_docx(build))
    assert [(e.kind, e.level, e.heading_path) for e in document.elements] == [
        (ElementKind.HEADING, 1, ()),
        (ElementKind.HEADING, 2, ("员工手册",)),
        (ElementKind.PARAGRAPH, 0, ("员工手册", "考勤")),
    ]


def test_docx_custom_style_based_on_heading():
    def build(d):
        style = d.styles.add_style("公司标题", WD_STYLE_TYPE.PARAGRAPH)
        style.base_style = d.styles["Heading 1"]
        d.add_paragraph("自定义样式的标题", style="公司标题")
        d.add_paragraph("正文")
    document = DocxParser().parse(_docx(build))
    assert document.elements[0].kind is ElementKind.HEADING


def test_docx_numbering_from_list_styles():
    # 内置的"列表编号 / 项目符号"样式,编号写在样式上,不在段落上
    def build(d):
        d.add_paragraph("第一步", style="List Number")
        d.add_paragraph("注意事项", style="List Bullet")
    document = DocxParser().parse(_docx(build))
    assert [(e.kind, e.level, e.ordered, e.text) for e in document.elements] == [
        (ElementKind.LIST_ITEM, 1, True, "第一步"),
        (ElementKind.LIST_ITEM, 1, False, "注意事项"),
    ]


def test_docx_table_in_document_order():
    def build(d):
        d.add_paragraph("表格前")
        table = d.add_table(rows=2, cols=2)
        table.cell(0, 0).text, table.cell(0, 1).text = "商品", "销量"
        table.cell(1, 0).text, table.cell(1, 1).text = "面膜", "4548"
        d.add_paragraph("表格后")
    document = DocxParser().parse(_docx(build))
    assert [e.kind for e in document.elements] == [ElementKind.PARAGRAPH, ElementKind.TABLE, ElementKind.PARAGRAPH]
    assert document.elements[1].rows == (("商品", "销量"), ("面膜", "4548"))


def test_broken_docx_is_rejected():
    with pytest.raises(DocumentParseError, match="无法打开 docx"):
        DocxParser().parse(b"PK\x03\x04 not really a zip")


# ---------------------------------------------------------------------------
# 按扩展名选解析器
# ---------------------------------------------------------------------------
def test_parser_chosen_by_extension_case_insensitive():
    # 上传目录里的文件名是 "<uploadId>-原文件名"
    assert isinstance(parser_for("0f8c-风控问题处理流程.PDF"), PdfParser)
    assert isinstance(parser_for("a.Docx"), DocxParser)
    assert isinstance(parser_for("a.md"), MarkdownParser)


@pytest.mark.parametrize("filename", ["旧版文档.doc", "说明.txt", "没有扩展名"])
def test_unsupported_formats_are_rejected(filename: str):
    with pytest.raises(UnsupportedFormatError, match="docx"):
        parser_for(filename)
