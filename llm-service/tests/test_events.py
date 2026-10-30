"""Tests for the message contract (the Python half).

The two that matter most:
  1. every example in the contract file parses; Java's UploadEventContractTest checks the same file
  2. the enum values match the CHECK constraints in db/migration/: drift detection for
     "one fact stored three times in three languages"
"""
import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.consumers.events import ContentKind, DocType, Source, TableType, UploadEvent

REPO_ROOT = Path(__file__).resolve().parents[2]  # tests/ -> llm-service/ -> repository root
CONTRACT = json.loads((REPO_ROOT / "contracts" / "upload-event.examples.json").read_text(encoding="utf-8"))


def _migration_version(path: Path) -> tuple[int, ...]:
    """V3__xxx.sql -> (3,); V3_1__xxx.sql / V3.1__xxx.sql -> (3, 1).
    Compared as numbers so V10 sorts after V9 (as strings, "V10" < "V9")."""
    return tuple(int(part) for part in re.split(r"[._]", path.name[1:].split("__", 1)[0]))


MIGRATIONS = sorted((REPO_ROOT / "db" / "migration").glob("V*__*.sql"), key=_migration_version)
# All migrations concatenated in version order: the complete history of the current schema
SCHEMA_SQL = "\n".join(path.read_text(encoding="utf-8") for path in MIGRATIONS)


def base(**overrides) -> dict:
    """A valid sop event; tests change one or two fields of it."""
    event = {
        "uploadId": "u-1", "storageKey": "u-1-a.md", "contentKind": "document", "docType": "sop",
        "tableType": None, "department": "ALL", "campaignId": None, "category": None, "source": "manual",
    }
    event.update(overrides)
    return event


# ------------------------------------------------------------------ contract file


@pytest.mark.parametrize("example", CONTRACT, ids=lambda e: e["uploadId"][:8])
def test_every_contract_example_parses(example):
    event = UploadEvent.model_validate(example)
    assert event.upload_id == example["uploadId"]
    assert event.storage_key == example["storageKey"]


def test_contract_examples_cover_every_enum_value():
    """The contract examples must cover every enum value, or a newly added value goes unchecked on both sides."""
    events = [UploadEvent.model_validate(e) for e in CONTRACT]
    assert {e.content_kind for e in events} == set(ContentKind)
    assert {e.doc_type for e in events if e.doc_type} == set(DocType)
    assert {e.table_type for e in events if e.table_type} == set(TableType)
    assert {e.source for e in events} == set(Source)


# ------------------------------------------------------------------ cross-language drift detection


def test_migrations_are_found():
    # If the folder or the naming rule changes, the tests below would pass vacuously on nothing parsed; stop that here
    assert [p.name.split("__")[0] for p in MIGRATIONS][:3] == ["V1", "V2", "V3"]


@pytest.mark.parametrize(("column", "enum"), [
    ("doc_type", DocType),        # in both documents and uploads
    ("table_type", TableType),    # uploads
    ("content_kind", ContentKind),  # uploads
    ("source", Source),           # uploads
])
def test_enum_values_match_every_sql_check_constraint(column, enum):
    # Limitation: if a later migration changes a CHECK's values, the old definition still sits in an
    # earlier migration and this reports a false mismatch. Then switch to the latest definition only,
    # or read the constraints from a real database.
    checks = re.findall(rf"CHECK\s*\(\s*{column}\s+IN\s*\(([^)]*)\)\s*\)", SCHEMA_SQL)
    assert checks, f"no CHECK constraint for {column} in the migrations; the regex or the SQL changed"
    for check in checks:
        assert set(re.findall(r"'([^']+)'", check)) == {v.value for v in enum}, (
            f"the CHECK constraint on {column} no longer matches the Python enum {enum.__name__}; "
            "when changing a database constraint, change events.py and the matching Java enum too"
        )


def test_table_type_values_are_real_tables_in_the_schema():
    tables = set(re.findall(r"CREATE TABLE (\w+)", SCHEMA_SQL)) - set(re.findall(r"DROP TABLE (\w+)", SCHEMA_SQL))
    for table_type in TableType:
        assert table_type.value in tables, f"TableType.{table_type.name} has no matching table in the SQL schema"


# ------------------------------------------------------------------ parsing behavior


def test_unknown_extra_fields_are_ignored_for_forward_compatibility():
    # When the producer adds a field, an older consumer must not crash ("tolerant reader")
    assert UploadEvent.model_validate(base(brandNewField="x")).upload_id == "u-1"


def test_accepts_snake_case_too_so_tests_and_scripts_can_build_events_naturally():
    event = UploadEvent.model_validate(
        {"upload_id": "u", "storage_key": "k", "content_kind": "document", "doc_type": "sop",
         "department": "ALL", "source": "manual"})
    assert event.doc_type is DocType.SOP


@pytest.mark.parametrize("bad", [
    pytest.param(base(uploadId=""), id="empty uploadId"),
    pytest.param(base(storageKey=""), id="empty storageKey"),
    pytest.param(base(contentKind="video"), id="unknown contentKind"),
    pytest.param(base(docType="SOP"), id="uppercase SOP (the wire format is lowercase)"),
    pytest.param(base(docType="campain_plan"), id="misspelled docType"),
    pytest.param(base(source="robot"), id="unknown source"),
    pytest.param(base(docType=None), id="document without docType"),
    pytest.param(base(docType="campaign_review", campaignId=None), id="review without campaignId"),
    pytest.param(base(campaignId=3), id="sop with a campaignId"),
    pytest.param(base(contentKind="data_table", docType=None, tableType=None, campaignId=3), id="data_table without tableType"),
    pytest.param(base(contentKind="data_table", docType=None, tableType="campaign_products", campaignId=None), id="data_table without campaignId"),
    pytest.param({"uploadId": "u"}, id="most required fields missing"),
    pytest.param("not an object", id="not a JSON object at all"),
])
def test_contract_violations_are_rejected(bad):
    with pytest.raises(ValidationError):
        UploadEvent.model_validate(bad)


def test_only_sop_can_go_without_a_campaign():
    assert DocType.SOP.needs_campaign is False
    assert DocType.CAMPAIGN_PLAN.needs_campaign is True
    assert DocType.CAMPAIGN_REVIEW.needs_campaign is True
