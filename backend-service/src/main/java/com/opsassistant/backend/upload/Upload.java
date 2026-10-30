package com.opsassistant.backend.upload;

import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.Instant;
import java.util.UUID;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

/**
 * One row of the uploads table: an upload and how far its processing got.
 *
 * The backend writes the row (status received) BEFORE publishing to Kafka, so by the time the consumer
 * gets the message the row is guaranteed to exist and the consumer can move it on to processing ->
 * done / failed. The status is visible from the moment the upload is accepted.
 *
 * The enum-like columns (source, contentKind, docType, tableType, status) are stored as the same
 * lowercase strings as the Kafka message; the database CHECK constraints guard the allowed values.
 * Review columns (reviewed_by, reviewed_at, review_comment) are not mapped yet; the approval flow will add them.
 */
@Entity
@Table(name = "uploads")
@Getter
@Setter
@NoArgsConstructor
public class Upload {

    public static final String RECEIVED = "received";
    public static final String FAILED = "failed";

    @Id
    private UUID uploadId;

    private String source;
    private Long uploadedBy;
    private String contentKind;
    private String docType;
    private String tableType;
    private String department;
    private Long campaignId;
    private String title;
    private String originalFilename;
    private String storageKey;
    private UUID replacesUploadId;
    private String status;
    private String error;
    private Instant createdAt;
    private Instant updatedAt;

    /** Moves the upload to another status and records why (error may be null). */
    public void changeStatus(String newStatus, String newError) {
        status = newStatus;
        error = newError;
        updatedAt = Instant.now();
    }
}
