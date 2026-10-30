"""Python model of document-upload-events messages.

This is the Python half of the CONTRACT between backend-service (Java, the producer) and this
service (the consumer). The other half is Java's UploadEvent.java; the shared source of truth
is contracts/upload-event.examples.json at the repository root, which both sides' tests
validate against (see tests/test_events.py).

The enum values below must agree in three places: the Java enums, the CHECK constraints in
db/migration/, and here. "One fact stored three times in three languages" drifts sooner or
later, so tests/test_events.py parses the migration files in db/migration/ and compares their
values with these; if someone changes the SQL and forgets this file, the test fails.
"""
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel


class ContentKind(StrEnum):
    DOCUMENT = "document"      # SOP / plan / review: free text, goes through RAG
    DATA_TABLE = "data_table"  # product table / customer behavior table: structured, parsed straight into rows


class DocType(StrEnum):
    SOP = "sop"
    CAMPAIGN_PLAN = "campaign_plan"
    CAMPAIGN_REVIEW = "campaign_review"

    @property
    def needs_campaign(self) -> bool:
        """Same rule as Java's DocType.needsCampaign(): only sop has no campaign."""
        return self is not DocType.SOP


class TableType(StrEnum):
    CAMPAIGN_PRODUCTS = "campaign_products"
    CAMPAIGN_CUSTOMER_BEHAVIOR = "campaign_customer_behavior"


class Source(StrEnum):
    MANUAL = "manual"
    AUTO = "auto"


class UploadEvent(BaseModel):
    """Java sends camelCase (uploadId); this model uses snake_case attributes (upload_id) and
    alias_generator=to_camel translates between them.

    Unknown fields are ignored (pydantic's default; no extra="forbid"). This is the "tolerant
    reader": when the producer adds a field, an older consumer should not crash. Missing
    required fields or unknown enum values, on the other hand, are rejected strictly, because
    they mean the contract really is broken.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    upload_id: str = Field(min_length=1)
    storage_key: str = Field(min_length=1)
    content_kind: ContentKind
    doc_type: DocType | None = None
    table_type: TableType | None = None
    department: str = Field(min_length=1)
    campaign_id: int | None = None
    category: str | None = None
    source: Source

    @model_validator(mode="after")
    def _check_business_rules(self) -> "UploadEvent":
        """The producer (UploadController) already validated these; checking again is defensive:
        other producers will come (a scheduled automatic upload, say) and we can't assume they
        all get it right."""
        if self.content_kind is ContentKind.DOCUMENT:
            if self.doc_type is None:
                raise ValueError("docType is required when contentKind=document")
            if self.doc_type.needs_campaign and self.campaign_id is None:
                raise ValueError(f"docType={self.doc_type.value} requires a campaignId")
            if not self.doc_type.needs_campaign and self.campaign_id is not None:
                raise ValueError("docType=sop must not have a campaignId")
        else:  # DATA_TABLE
            if self.table_type is None:
                raise ValueError("tableType is required when contentKind=data_table")
            if self.campaign_id is None:
                raise ValueError("contentKind=data_table requires a campaignId")
        return self
