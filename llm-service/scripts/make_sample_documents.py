"""用本机的文字处理软件把 Markdown 样例转成 docx 和 PDF,给解析层的测试当样例。

只能在装了 Word 或 WPS 的 Windows 上跑。流程:
    Markdown --(markdown-it-py)--> HTML --(导入)--> 另存为 docx、另存为 PDF
导入 HTML 时 <h1>/<h2> 变成内置的"标题 1 / 标题 2"样式、<ol>/<ul> 变成自动编号列表、
<table> 变成表格,跟人在软件里排版得到的文件结构一样。

通过 COM 接口 Word.Application 调用。同时装了 WPS 的机器上,WPS 会把这个接口的 64 位注册
指向自己(wps.exe),所以现在仓库里的样例实际是 WPS 生成的:docx 里写着 "WPS Office",
PDF 的 Creator 是 "WPS 文字"。两种软件存出来的都是标准 docx / PDF,解析器不区分。
WPS 导入时有一处改动:编号列表里嵌套的项目符号列表,被它改成了编号(PDF 里能看到 "1. 申诉通过")。

生成的文件提交进仓库(tests/fixtures/documents/),跑测试不需要 Word。
Markdown 原文改了之后要重新跑一次,否则"三种格式解析结果一致"的测试会失败。

跑法:
    cd llm-service
    uv run python -m scripts.make_sample_documents
"""
import re
import subprocess
import tempfile
from pathlib import Path

from markdown_it import MarkdownIt

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = REPO_ROOT / "llm-service" / "tests" / "fixtures" / "documents"

SOURCES = [
    REPO_ROOT / "sop_docs" / "风控问题处理流程.md",           # 有两级列表
    REPO_ROOT / "sop_docs" / "主播签约考核流程-3C数码.md",
    REPO_ROOT / "campaign_docs" / "美妆618大促-复盘.md",      # 有表格
]

# 真实的 Word 文档没有 YAML frontmatter,转换前去掉
FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)

# 表格加上边框:Word 里新建表格默认就有边框;没有边框的话,PDF 里也就没有表格线
HTML_TEMPLATE = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>table {{ border-collapse: collapse; }} th, td {{ border: 1px solid #000; padding: 2px 6px; }}</style>
</head><body>
{body}
</body></html>
"""

# Word 的 COM 接口只能从 Windows 程序里调,PowerShell 是最省事的一个。
# 文件路径(含中文)用参数传进去,脚本本身只能有 ASCII:PowerShell 5.1 把没有 BOM 的
# 脚本按系统 ANSI 编码读,写中文注释会读成乱码。所以说明都写在这里:
#   Documents.Add()   基于默认模板新建,跟用户新建一个空白文档一样
#   SaveAs2(..., 16)  wdFormatDocumentDefault,即 .docx
#   SaveAs2(..., 17)  wdFormatPDF,等于"另存为 PDF"
#   Quit 包在 try 里:实测关掉最后一个文档后,这台机器上的 Word 进程有时已经自己退出了,
#   这时再调 Quit 会报 RPC 失败(0x800706BE);文件此前已经保存好,不影响结果
WORD_SCRIPT = r"""
param([string]$Html, [string]$Docx, [string]$Pdf)
$ErrorActionPreference = 'Stop'
$word = New-Object -ComObject Word.Application
$word.Visible = $false
$word.DisplayAlerts = 0
try {
    $doc = $word.Documents.Add()
    $doc.Range().InsertFile($Html)
    $doc.SaveAs2($Docx, 16)
    $doc.SaveAs2($Pdf, 17)
    $doc.Close($false)
} finally {
    try { $word.Quit() } catch { }
    [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($word)
}
"""


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    md = MarkdownIt("commonmark").enable("table")
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "convert.ps1"
        script.write_text(WORD_SCRIPT, encoding="ascii")
        for source in SOURCES:
            body = md.render(FRONTMATTER.sub("", source.read_text(encoding="utf-8")))
            html = Path(tmp) / f"{source.stem}.html"
            html.write_text(HTML_TEMPLATE.format(body=body), encoding="utf-8")
            docx = OUTPUT_DIR / f"{source.stem}.docx"
            pdf = OUTPUT_DIR / f"{source.stem}.pdf"
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-File", str(script), str(html), str(docx), str(pdf)],
                check=True,
            )
            print(f"{source.name} -> {docx.name}, {pdf.name}")


if __name__ == "__main__":
    main()
