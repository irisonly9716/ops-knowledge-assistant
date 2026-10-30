package com.opsassistant.backend.upload;

/**
 * The message body published to the Kafka topic document-upload-events.
 *
 * This is the CONTRACT between backend-service (Java, the producer) and llm-service (Python, the
 * consumer). Changing a field or the format means changing both sides and updating
 * contracts/upload-event.examples.json; both sides' tests validate against that one file, so the
 * two sides cannot drift apart unnoticed (see UploadEventContractTest and
 * llm-service/tests/test_events.py).
 *
 * storageKey is not a file path but a logical key in storage (today: the file name). A file path
 * relative to backend-service's own folder (like "uploads\xxx.md") means nothing to a consumer
 * running in another folder, let alone on another machine. The message carries only the logical
 * key; each service knows where the storage root is (the UPLOAD_DIR environment variable), and
 * moving to S3 later keeps the key and only swaps the storage backend.
 *
 * contentKind/docType/tableType/source are enums, not raw strings, so the Java side can't
 * misspell a value. Serialized to JSON (see each enum's @JsonValue) they are still the agreed
 * lowercase strings ("document"/"sop"...), matching llm-service (Python) and the database CHECK
 * constraints; the enums make Java safer internally without changing the wire format.
 *
 * A record is Java shorthand: declare the fields once and get a constructor, accessors (named like
 * the field, no get prefix: uploadId(), not getUploadId()) and equals/hashCode/toString, with every
 * field final (immutable after creation).
 */
public record UploadEvent(
        String uploadId,
        String storageKey,
        ContentKind contentKind,
        DocType docType,       // set only when contentKind=DOCUMENT
        TableType tableType,   // set only when contentKind=DATA_TABLE
        String department,
        Long campaignId,       // nullable: an sop belongs to no campaign
        String category,       // nullable
        Source source
) {
}
