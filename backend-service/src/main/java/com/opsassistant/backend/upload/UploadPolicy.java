package com.opsassistant.backend.upload;

import com.opsassistant.backend.user.User;

/**
 * The permission rules of docs/permission-model.md for uploads, as plain functions: no database, no web,
 * so the whole matrix is unit-tested on its own.
 *
 * In one sentence: staff and managers upload; a new document for their own department takes effect at once;
 * replacing anything, or anything for ALL, needs approval from the uploader's superior, who must be able to
 * see the content.
 */
final class UploadPolicy {

    static final String STAFF = "staff";
    static final String MANAGER = "manager";
    static final String EXECUTIVE = "executive";
    static final String ADMIN = "admin";
    static final String ALL_DEPARTMENTS = "ALL";

    private UploadPolicy() {
    }

    /** Documents and data tables are business content: executive and admin never upload (separation of duties). */
    static boolean canUpload(User user) {
        return STAFF.equals(user.getRole()) || MANAGER.equals(user.getRole());
    }

    static boolean needsApproval(boolean replacing, String department) {
        return replacing || ALL_DEPARTMENTS.equals(department);
    }

    /**
     * The uploader's superior: a manager for the staff of their own department, the executive for managers.
     * Another department's manager can't see department-only documents, so a manager's request can only go
     * to the executive, who sees everything. Admin never approves: it keeps the system, not the content.
     */
    static boolean canReview(User reviewer, User uploader) {
        return switch (reviewer.getRole()) {
            case MANAGER -> STAFF.equals(uploader.getRole())
                    && reviewer.getDepartmentId() != null
                    && reviewer.getDepartmentId().equals(uploader.getDepartmentId());
            case EXECUTIVE -> MANAGER.equals(uploader.getRole());
            default -> false;
        };
    }

    /**
     * Upload records are system data: the uploader sees their own, whoever may review it sees it,
     * and admin sees all of them. The executive sees no other upload records, like the audit log.
     */
    static boolean canSeeStatus(User viewer, User uploader) {
        return ADMIN.equals(viewer.getRole())
                || viewer.getUserId().equals(uploader.getUserId())
                || canReview(viewer, uploader);
    }
}
