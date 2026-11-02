package com.opsassistant.backend.upload;

import java.util.UUID;

/** What the upload rules decided about an upload request. */
public record UploadTarget(
        String department,        // derived from the uploader and the scope, never taken from the request
        UUID replacesUploadId,    // the current version this upload replaces; null for a new one, or seeded data
        boolean needsApproval
) {
}
