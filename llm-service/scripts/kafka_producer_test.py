"""Publish one contract-compliant upload event to document-upload-events by hand, bypassing the
Java backend, to check the consumer on its own. Run it with -m, not with a file path (see below):

    cd llm-service
    uv run python -m scripts.kafka_producer_test

It does what the backend does: writes a file into the upload folder, records the upload in the
uploads table (status received, uploaded by the first staff user), then publishes a valid event
pointing at it. The consumer should ingest it end to end: the whole Python side of the pipeline in
one check. (The consumer rejects a message without an uploads row, and a made-up message that breaks
contracts/upload-event.examples.json ends up in the dead-letter topic.)

Each run creates a real SOP titled "Kafka smoke test <id>" for ALL departments (with real embeddings,
a fraction of a cent). Remove them afterwards with
    DELETE FROM documents WHERE title LIKE 'Kafka smoke test %';

Why `-m scripts.xxx` and not `python scripts/xxx.py`: the latter sets sys.path[0] to the
scripts/ folder itself, so `import app.config` can't find the app package. With `-m`, Python
uses the current directory (llm-service/) as the root, where app/ and scripts/ sit side by side.
"""
import json
import sys
import uuid
from pathlib import Path

from confluent_kafka import Producer

from app import db
from app.config import settings

BOOTSTRAP_SERVERS = settings.kafka_bootstrap_servers   # a script on the host connects to the EXTERNAL listener
TOPIC = settings.doc_upload_topic


def delivery_report(err, msg):
    """Kafka sends asynchronously: produce() only puts the message into a local buffer, and this
    callback runs once the broker has it (or it failed). So produce() not raising proves nothing.
    """
    if err is not None:
        print(f"Delivery FAILED: {err}", file=sys.stderr)
    else:
        print(f"Delivered -> topic={msg.topic()} partition={msg.partition()} offset={msg.offset()}")


def main():
    upload_id = str(uuid.uuid4())
    storage_key = f"{upload_id}-smoke-test.md"

    # Put the file into the upload folder first (backend-service does this in production)
    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    (upload_dir / storage_key).write_text(
        "# Kafka smoke test\n\nWritten to the upload folder by kafka_producer_test.py.\n", encoding="utf-8")

    # Then the uploads row, before publishing: the consumer looks the upload up by its id
    title = f"Kafka smoke test {upload_id[:8]}"
    with db.connect() as conn:
        uploader = conn.execute("SELECT user_id FROM users WHERE role = 'staff' ORDER BY user_id LIMIT 1").fetchone()[0]
        conn.execute(
            """INSERT INTO uploads (upload_id, source, uploaded_by, content_kind, doc_type, department, title,
                                    original_filename, storage_key, status)
               VALUES (%s, 'manual', %s, 'document', 'sop', 'ALL', %s, 'smoke-test.md', %s, 'received')""",
            (upload_id, uploader, title, storage_key))

    producer = Producer({"bootstrap.servers": BOOTSTRAP_SERVERS})
    event = {  # field names and values must match contracts/upload-event.examples.json (camelCase)
        "uploadId": upload_id,
        "storageKey": storage_key,
        "contentKind": "document",
        "docType": "sop",
        "tableType": None,
        "department": "ALL",
        "campaignId": None,
        "category": "Smoke test",
        "source": "manual",
    }
    producer.produce(
        TOPIC,
        key=upload_id,
        value=json.dumps(event).encode("utf-8"),
        callback=delivery_report,
    )

    # flush() blocks until everything in the buffer is sent (or the timeout hits). A script that
    # exits right away must call it, or the process can end before the message actually leaves.
    producer.flush(timeout=10)


if __name__ == "__main__":
    main()
