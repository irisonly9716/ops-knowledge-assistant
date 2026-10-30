"""Tests for chunking. They use the real tokenizer: tiktoken downloads its encoding file on first use
and caches it, so only the very first run needs the network.

The acceptance criteria come first: every SOP step is whole inside one chunk, sections are never mixed,
and a unit too large for one chunk is split with overlap.
"""
from datetime import date
from pathlib import Path

import pytest

from app.rag.chunking import (
    MAX_CHUNK_TOKENS,
    DocumentInfo,
    chunk_document,
    chunk_header,
    count_tokens,
    sop_steps,
)
from app.rag.document_fields import document_fields
from app.rag.parsing import Element, ElementKind, parse_file
from app.rag.parsing.elements import finalize, table_element

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "documents"
SOPS = sorted((REPO_ROOT / "sop_docs").glob("*.md"))
SOPS.remove(REPO_ROOT / "sop_docs" / "README.md")
REVIEW = REPO_ROOT / "campaign_docs" / "beauty-black-friday-live-review.md"

SOP_INFO = DocumentInfo(type_label="SOP", department_label="All departments", title="Some SOP")
REVIEW_INFO = DocumentInfo(type_label="Post-campaign review", department_label="Beauty",
                           title="Beauty Black Friday Live - Post-campaign review",
                           campaign_name="Beauty Black Friday Live", start_date=date(2025, 11, 18),
                           host_name="Tommy Walter")


def _heading(text: str, level: int) -> Element:
    return Element(kind=ElementKind.HEADING, text=text, level=level)


def _step(text: str, level: int = 1, ordered: bool = True) -> Element:
    return Element(kind=ElementKind.LIST_ITEM, text=text, level=level, ordered=ordered)


def _paragraph(text: str) -> Element:
    return Element(kind=ElementKind.PARAGRAPH, text=text)


# ---------------------------------------------------------------------------
# Acceptance: steps stay whole, sections stay apart
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("sop", SOPS, ids=lambda path: path.stem)
def test_every_sop_step_is_whole_inside_exactly_one_chunk(sop: Path):
    document = parse_file(sop)
    chunks = chunk_document(document, SOP_INFO)
    steps = sop_steps(document)
    assert len(steps) >= 7
    for step in steps:
        line = f"{step['order']}. {step['text']}"
        assert sum(line in chunk.content for chunk in chunks) == 1, line


@pytest.mark.parametrize("sop", SOPS, ids=lambda path: path.stem)
def test_one_chunk_per_section_at_the_default_limit(sop: Path):
    # Section-level chunking: every sample SOP section fits into one chunk, so the whole procedure
    # comes back together when someone asks "how do I handle X"
    document = parse_file(sop)
    sections = {e.heading_path for e in document.elements if e.kind is not ElementKind.HEADING}
    chunks = chunk_document(document, SOP_INFO)
    assert sorted(c.heading_path for c in chunks) == sorted(sections)
    assert all(c.token_count <= MAX_CHUNK_TOKENS for c in chunks)


def test_a_long_section_is_split_between_steps_and_keeps_numbering():
    steps = [_step(f"Step title {i}: " + "check the stock level of every listed product carefully. " * 4)
             for i in range(1, 21)]
    document = finalize([_heading("Big SOP", 1), _heading("Procedure", 2), *steps])
    chunks = chunk_document(document, SOP_INFO, max_tokens=300)

    assert len(chunks) > 1
    assert all(c.token_count <= 300 for c in chunks)
    assert {c.heading_path for c in chunks} == {("Big SOP", "Procedure")}   # every piece knows its section
    for i, step in enumerate(steps, start=1):
        # whole, exactly once, with its own number: a later chunk continues at 8., 9.... instead of restarting at 1.
        assert sum(f"{i}. {step.text}" in c.content for c in chunks) == 1


def test_nested_items_travel_with_their_step():
    document = finalize([
        _heading("SOP", 1),
        _step("Follow up: " + "write down everything that happened during the review. " * 6),
        _step("Approved: restore every feature.", level=2, ordered=False),
        _step("Rejected: escalate to the lead.", level=2, ordered=False),
        _step("Archive the case: " + "store the screenshots in the shared library for later. " * 6),
    ])
    chunks = chunk_document(document, SOP_INFO, max_tokens=120)
    first = next(c for c in chunks if "1. Follow up" in c.content)
    assert "   - Approved: restore every feature.\n   - Rejected: escalate to the lead." in first.content
    assert "2. Archive the case" not in first.content


def test_an_oversized_paragraph_is_split_with_overlap():
    sentences = [f"Sentence {i} explains one more detail of the appeal process." for i in range(60)]
    document = finalize([_heading("SOP", 1), _paragraph(" ".join(sentences))])
    chunks = chunk_document(document, SOP_INFO, max_tokens=150, overlap_tokens=30)

    assert len(chunks) > 3
    assert all(c.token_count <= 150 for c in chunks)
    bodies = [c.content.split("\n\n", 1)[1] for c in chunks]
    for previous, current in zip(bodies, bodies[1:]):
        assert previous.split(". ")[-1].rstrip(".") in current   # the next piece starts with the end of the previous one
    assert all(any(s in body for body in bodies) for s in sentences)   # nothing lost


def test_an_oversized_table_is_split_by_rows_with_the_header_repeated():
    rows = [("Product", "Units sold", "Sales")] + [(f"Product {i}", str(100 + i), f"${i}.5K") for i in range(80)]
    document = finalize([_heading("Review", 1), table_element(rows)])
    chunks = chunk_document(document, REVIEW_INFO, max_tokens=200)

    assert len(chunks) > 1
    assert all(c.token_count <= 200 for c in chunks)
    assert all("| Product | Units sold | Sales |\n|---|---|---|" in c.content for c in chunks)
    assert sum(c.content.count("| Product ") for c in chunks) == 80 + len(chunks)   # each row once + one header per piece


# ---------------------------------------------------------------------------
# What a chunk looks like
# ---------------------------------------------------------------------------
def test_chunk_starts_with_the_contextual_header():
    document = parse_file(SOPS[0])   # account-restriction-handling.md
    procedure = next(c for c in chunk_document(document, SOP_INFO) if c.heading_path[-1] == "Procedure")
    assert procedure.content.startswith(
        "SOP | All departments\nAccount Restriction Handling > Procedure\n\n"
        "1. Identify the rule and the account: Log in to the platform's seller center")
    assert "\n7. Follow up on the result:\n   - Appeal approved:" in procedure.content


def test_campaign_header_names_the_campaign_date_and_host():
    assert chunk_header(REVIEW_INFO, ("Beauty Black Friday Live - Post-Campaign Review", "Product Breakdown")) == (
        "Post-campaign review | Beauty | Beauty Black Friday Live | 2025-11-18 | Host: Tommy Walter\n"
        "Beauty Black Friday Live - Post-Campaign Review > Product Breakdown")


def test_text_before_any_heading_is_labelled_with_the_title():
    document = finalize([_paragraph("A note without any heading.")])
    [chunk] = chunk_document(document, SOP_INFO)
    assert chunk.content == "SOP | All departments\nSome SOP\n\nA note without any heading."


def test_tables_become_markdown_tables():
    chunks = chunk_document(parse_file(REVIEW), REVIEW_INFO)
    products = next(c for c in chunks if c.heading_path[-1] == "Product Breakdown")
    assert "| Product | Units sold | Sales | Profit | Margin | Return rate |\n|---|---|---|---|---|---|\n" \
           "| Sheet Mask | 470 | $131.6K | $45.4K | 34.52% | 10.2% |" in products.content


def test_token_count_and_metadata():
    chunks = chunk_document(parse_file(FIXTURES / "beauty-black-friday-live-review.pdf"), REVIEW_INFO)
    assert [c.index for c in chunks] == list(range(len(chunks)))
    assert all(c.token_count == count_tokens(c.content) for c in chunks)
    products = next(c for c in chunks if c.heading_path[-1] == "Product Breakdown")
    assert products.metadata() == {"heading_path": list(products.heading_path), "pages": [1, 2],
                                   "token_count": products.token_count}   # the section spans pages 1-2


@pytest.mark.parametrize("stem", ["host-onboarding-and-review-electronics", "beauty-black-friday-live-review"])
@pytest.mark.parametrize("suffix", [".docx", ".pdf"])
def test_same_chunks_from_markdown_docx_and_pdf(stem: str, suffix: str):
    # account-restriction-handling is left out: WPS turned its nested bullets into numbers
    markdown = next(REPO_ROOT.glob(f"*_docs/{stem}.md"))
    expected = [c.content for c in chunk_document(parse_file(markdown), REVIEW_INFO)]
    actual = [c.content for c in chunk_document(parse_file(FIXTURES / f"{stem}{suffix}"), REVIEW_INFO)]
    assert actual == expected


# ---------------------------------------------------------------------------
# documents.steps
# ---------------------------------------------------------------------------
def test_sop_steps_for_the_documents_steps_column():
    steps = sop_steps(parse_file(SOPS[0]))
    assert [s["order"] for s in steps] == [1, 2, 3, 4, 5, 6, 7]
    assert steps[0]["section"] == "Account Restriction Handling > Procedure"
    assert steps[2]["text"].startswith("Collect appeal evidence:")
    assert steps[6]["substeps"][1].startswith("Appeal rejected:")
    assert all(not s["substeps"] for s in steps[:6])


def test_numbering_restarts_after_a_paragraph_and_bullets_are_not_steps():
    document = finalize([
        _heading("Review", 1),
        _step("first finding"), _step("second finding"),
        _paragraph("Next list:"),
        _step("again one"),
        _step("a bullet", ordered=False),
    ])
    assert [(s["order"], s["text"]) for s in sop_steps(document)] == [(1, "first finding"), (2, "second finding"),
                                                                      (1, "again one")]


# ---------------------------------------------------------------------------
# documents fields: the upload decides, the frontmatter fills gaps
# ---------------------------------------------------------------------------
def test_frontmatter_never_overrides_title_or_department():
    fields, warnings = document_fields(title="Inventory Check", department="Fresh Grocery", category=None,
                                       frontmatter={"title": "Inventory Check", "department": "ALL",
                                                    "category": "Inventory", "responsible_team": "Supply Chain team"})
    assert (fields.title, fields.department) == ("Inventory Check", "Fresh Grocery")
    assert (fields.category, fields.responsible_team) == ("Inventory", "Supply Chain team")
    assert warnings == ["frontmatter department 'ALL' ignored; the upload says 'Fresh Grocery'"]


def test_frontmatter_matching_up_to_case_and_spaces_is_not_a_warning():
    _, warnings = document_fields(title="Account Restriction Handling", department="ALL", category="Trust & Safety",
                                  frontmatter={"title": "  account restriction handling ", "department": "all",
                                               "category": "trust & safety"})
    assert warnings == []


def test_documents_without_frontmatter_get_no_extra_fields():
    fields, warnings = document_fields(title="Plan", department="Beauty", category=None, frontmatter={})
    assert (fields.category, fields.responsible_team, warnings) == (None, None, [])
