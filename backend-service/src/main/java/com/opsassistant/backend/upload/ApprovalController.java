package com.opsassistant.backend.upload;

import com.opsassistant.backend.user.CurrentUser;
import com.opsassistant.backend.user.User;
import java.util.List;
import java.util.UUID;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

/**
 * The approval queue: what the current user has to decide, and the decision. A manager reviews the staff of
 * their own department, the executive reviews managers (docs/permission-model.md). While an upload waits, the
 * old version stays in effect; nothing is published until it is approved.
 */
@RestController
@RequestMapping("/documents/approvals")
@RequiredArgsConstructor
public class ApprovalController {

    private final UploadService uploadService;
    private final UploadStorage storage;

    @GetMapping
    public List<UploadStatusResponse> pending(@CurrentUser User reviewer) {
        return uploadService.pendingFor(reviewer).stream().map(UploadStatusResponse::of).toList();
    }

    @PostMapping("/{uploadId}/approve")
    public UploadStatusResponse approve(@CurrentUser User reviewer, @PathVariable UUID uploadId,
                                        @RequestParam(value = "comment", required = false) String comment) {
        Upload upload = uploadService.approve(uploadId, reviewer, comment);
        if (Upload.REJECTED.equals(upload.getStatus())) {
            // The request went stale while it waited. Its file is of no use, and the decision is already committed
            storage.deleteQuietly(upload.getStorageKey());
            throw new ResponseStatusException(HttpStatus.CONFLICT, upload.getError());
        }
        return UploadStatusResponse.of(upload);
    }

    @PostMapping("/{uploadId}/reject")
    public UploadStatusResponse reject(@CurrentUser User reviewer, @PathVariable UUID uploadId,
                                       @RequestParam(value = "comment", required = false) String comment) {
        Upload upload = uploadService.reject(uploadId, reviewer, comment);
        storage.deleteQuietly(upload.getStorageKey());   // only now that the decision is committed
        return UploadStatusResponse.of(upload);
    }
}
