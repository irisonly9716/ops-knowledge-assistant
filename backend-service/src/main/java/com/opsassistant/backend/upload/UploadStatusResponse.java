package com.opsassistant.backend.upload;

import java.time.Instant;
import java.util.UUID;

/** An upload as the API shows it: the status endpoint and the approval list. */
public record UploadStatusResponse(
        UUID uploadId,
        String status,
        String error,
        String contentKind,
        String docType,
        String tableType,
        String department,
        Long campaignId,
        String title,
        String originalFilename,
        Long uploadedBy,
        UUID replacesUploadId,
        Long reviewedBy,
        Instant reviewedAt,
        String reviewComment,
        Instant createdAt,
        Instant updatedAt
) {

    static UploadStatusResponse of(Upload upload) {
        return new UploadStatusResponse(upload.getUploadId(), upload.getStatus(), upload.getError(),
                upload.getContentKind(), upload.getDocType(), upload.getTableType(), upload.getDepartment(),
                upload.getCampaignId(), upload.getTitle(), upload.getOriginalFilename(), upload.getUploadedBy(),
                upload.getReplacesUploadId(), upload.getReviewedBy(), upload.getReviewedAt(), upload.getReviewComment(),
                upload.getCreatedAt(), upload.getUpdatedAt());
    }
}
