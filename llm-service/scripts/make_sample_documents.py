"""Convert the Markdown samples to docx and PDF with this machine's word processor; the results are
the fixtures for the parsing tests.

Runs only on Windows with Word or WPS installed. The flow:
    Markdown --(markdown-it-py)--> HTML --(import)--> save as docx, save as PDF
On import, <h1>/<h2> become the built-in "Heading 1 / Heading 2" styles, <ol>/<ul> become
automatic numbered lists and <table> becomes a table: the same structure a person gets by
formatting the document in the application.

It goes through the COM interface Word.Application. On a machine that also has WPS installed,
WPS points the 64-bit registration of that interface at itself (wps.exe), so the fixtures in the
repository were actually produced by WPS: the docx says "WPS Office" and the PDF's Creator is
WPS. Both applications save standard docx / PDF and the parsers don't care which one it was.
WPS changes one thing on import: a bullet list nested inside a numbered list becomes numbered.

The generated files are committed (tests/fixtures/documents/), so running the tests needs no Word.
Rerun this after changing a Markdown source, or the "all three formats parse the same" test fails.

Run:
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
    REPO_ROOT / "sop_docs" / "account-restriction-handling.md",            # two list levels
    REPO_ROOT / "sop_docs" / "host-onboarding-and-review-electronics.md",
    REPO_ROOT / "campaign_docs" / "beauty-black-friday-live-review.md",     # tables
]

# Real Word documents have no YAML frontmatter; strip it before converting
FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)

# Give tables borders: a new table in Word has borders by default, and without them the PDF
# has no ruling lines either
HTML_TEMPLATE = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>table {{ border-collapse: collapse; }} th, td {{ border: 1px solid #000; padding: 2px 6px; }}</style>
</head><body>
{body}
</body></html>
"""

# Word's COM interface can only be called from a Windows program; PowerShell is the simplest.
# File paths are passed as arguments and the script itself stays ASCII-only: PowerShell 5.1
# reads a script without a BOM in the system ANSI code page, so non-ASCII text would be garbled.
# That is why the notes live here:
#   Documents.Add()   a new document from the default template, like a user creating a blank one
#   SaveAs2(..., 16)  wdFormatDocumentDefault, i.e. .docx
#   SaveAs2(..., 17)  wdFormatPDF, the same as "Save as PDF"
#   Quit is wrapped in try: on this machine the Word process has been seen to exit on its own
#   after its last document closes, and calling Quit then fails with an RPC error (0x800706BE);
#   the files were saved before that, so the result is unaffected
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
