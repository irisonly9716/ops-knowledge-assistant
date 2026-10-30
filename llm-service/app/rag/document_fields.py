"""The values a documents row gets: the upload decides, the Markdown frontmatter only fills gaps.

Title and department come from the upload form and the uploader's identity. The department decides
who can see the document and who has to approve it, so a file's content must never be able to
change it: a staff member writing "department: ALL" into a frontmatter would otherwise skip the
approval an ALL document needs. The frontmatter only supplies what the form doesn't ask for
(responsible_team, and category when the form left it empty). A frontmatter that disagrees with the
upload is ignored and reported as a warning, not rejected: most uploaders don't know what a
frontmatter is and would not understand the rejection. docx and PDF have no frontmatter at all.
"""
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DocumentFields:
    title: str
    department: str
    category: str | None
    responsible_team: str | None


def document_fields(*, title: str, department: str, category: str | None,
                    frontmatter: Mapping[str, Any]) -> tuple[DocumentFields, list[str]]:
    """(the fields to store, warnings about frontmatter values that were ignored)."""
    warnings = []
    for key, value in (("title", title), ("department", department), ("category", category)):
        claimed = _text(frontmatter.get(key))
        if value and claimed and _normalize(claimed) != _normalize(value):
            warnings.append(f"frontmatter {key} {claimed!r} ignored; the upload says {value!r}")
    fields = DocumentFields(
        title=title,
        department=department,
        category=category or _text(frontmatter.get("category")),
        responsible_team=_text(frontmatter.get("responsible_team")),
    )
    return fields, warnings


def _text(value: Any) -> str | None:
    """A frontmatter value as stripped text; YAML can also give numbers, lists or nothing at all."""
    if value is None or isinstance(value, (list, dict)):
        return None
    return str(value).strip() or None


def _normalize(text: str) -> str:
    # The same comparison the database uses for SOP titles (see uq_documents_sop_title in V3)
    return unicodedata.normalize("NFKC", text).strip().casefold()
