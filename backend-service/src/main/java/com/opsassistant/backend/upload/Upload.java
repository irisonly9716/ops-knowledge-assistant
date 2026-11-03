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
 * The backend writes the row in the same transaction as the outbox row carrying its event (UploadService),
 * and the event is only published after that commit, so by the time the consumer gets the message the row is
 * guaranteed to exist and the consumer can move it on to processing -> done / failed. An upload that needs
 * approval starts as pending_approval with no event at all; approving it writes the event. The status is
 * visible from the moment the upload is accepted.
 *
 * The enum-like columns (source, contentKind, docType, tableType, status) are stored as the same
 * lowercase strings as the Kafka message; the database CHECK constraints guard the allowed values.
 */
@Entity
@Table(name = "uploads")
@Getter
@Setter
@NoArgsConstructor
public class Upload {

    public static final String PENDING_APPROVAL = "pending_approval";
    public static final String RECEIVED = "received";
    public static final String PROCESSING = "processing";
    public static final String DONE = "done";
    public static final String FAILED = "failed";
    public static final String REJECTED = "rejected";

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
    private String category;
    private String originalFilename;
    private String storageKey;
    private UUID replacesUploadId;
    private String status;
    private String error;
    private Long reviewedBy;
    private Instant reviewedAt;
    private String reviewComment;
    private Instant createdAt;
    private Instant updatedAt;
    private int republishCount;
    private Instant lastRepublishedAt;
    // uploads.delivery_count is deliberately not mapped. The consumer writes it, and saving this entity writes every
    // mapped column, so a mapped copy could overwrite a count that went up in the meantime.

    /** Moves the upload to another status and records why (error may be null). */
    public void changeStatus(String newStatus, String newError) {
        status = newStatus;
        error = newError;
        updatedAt = Instant.now();
    }

    /** Records the review decision. The database refuses a reviewer who is the uploader (chk_uploads_not_self_review). */
    public void review(String newStatus, long reviewerId, String comment, String newError) {
        changeStatus(newStatus, newError);
        reviewedBy = reviewerId;
        reviewedAt = updatedAt;
        reviewComment = comment;
    }

    public boolean isPendingApproval() {
        return PENDING_APPROVAL.equals(status);
    }

    /** On its way through the consumer: it has an event and hasn't ended yet. */
    public boolean isOnItsWay() {
        return RECEIVED.equals(status) || PROCESSING.equals(status);
    }

    /** When this upload last showed any sign of moving. */
    public Instant lastActivity() {
        return lastRepublishedAt != null ? lastRepublishedAt : updatedAt;
    }

    public void recordRepublish(Instant at) {
        republishCount++;
        lastRepublishedAt = at;
    }
}
