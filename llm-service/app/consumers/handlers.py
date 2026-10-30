"""What to do once a valid upload event arrives.

The minimal version for now: it confirms the path "upload -> Kafka -> consumer receives it ->
file is readable" works. Next steps, branching on event.content_kind:
  - parsing + chunking
  - embedding
  - writing to pgvector
  - updating the processing status, with deduplication: Kafka delivers at least once, so the
    same message can arrive twice (the consumer crashes after processing but before committing
    the offset, and the message is redelivered on restart). Today's handler only reads, so it
    is naturally idempotent and duplicates are harmless; once it writes to the database it
    must deduplicate on upload_id.
"""
import logging

from app.consumers.events import UploadEvent
from app.consumers.storage import resolve_upload_path

log = logging.getLogger("upload_consumer")


def handle_upload(event: UploadEvent) -> None:
    path = resolve_upload_path(event.storage_key)
    data = path.read_bytes()
    preview = data[:60].decode("utf-8", errors="replace").replace("\n", " ")
    log.info(
        "received upload upload_id=%s kind=%s type=%s dept=%s campaign=%s file=%s (%d bytes) preview=%r",
        event.upload_id,
        event.content_kind.value,
        (event.doc_type or event.table_type).value,
        event.department,
        event.campaign_id,
        path.name,
        len(data),
        preview,
    )
