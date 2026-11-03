package com.opsassistant.backend.upload;

import com.opsassistant.backend.campaign.Campaign;
import com.opsassistant.backend.campaign.CampaignRepository;
import com.opsassistant.backend.department.Department;
import com.opsassistant.backend.department.DepartmentRepository;
import com.opsassistant.backend.document.Document;
import com.opsassistant.backend.document.DocumentRepository;
import com.opsassistant.backend.outbox.OutboxMessage;
import com.opsassistant.backend.outbox.OutboxRepository;
import com.opsassistant.backend.user.User;
import com.opsassistant.backend.user.UserRepository;
import java.time.Instant;
import java.util.List;
import java.util.Objects;
import java.util.UUID;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;
import tools.jackson.databind.ObjectMapper;

/**
 * The rules and the bookkeeping of uploads: what an upload is allowed to be, recording it, and the
 * approval decision. Rules that need no database are in UploadPolicy.
 *
 * An upload and its event are written in one transaction, either both or neither (the transactional
 * outbox), so an upload can never be recorded without its event. An upload waiting for approval has no event
 * at all: nothing reaches Kafka, the consumer or the current version of the document until it is approved,
 * which writes the event in the same transaction as the decision. Publishing happens later, in OutboxRelay;
 * no request here waits for Kafka or fails when Kafka is down.
 */
@Service
@RequiredArgsConstructor
public class UploadService {

    private final UploadRepository uploadRepository;
    private final OutboxRepository outboxRepository;
    private final DocumentRepository documentRepository;
    private final CampaignRepository campaignRepository;
    private final DepartmentRepository departmentRepository;
    private final DataTableRepository dataTableRepository;
    private final UserRepository userRepository;
    private final ObjectMapper objectMapper; // the Jackson instance Spring Boot auto-configures

    @Value("${document.upload-topic}")
    private String uploadTopic;

    // ------------------------------------------------------------------ what an upload is allowed to be

    /**
     * Checks a request against who is asking and what already exists. Writes nothing.
     * 403: not this uploader's to touch. 409: "new" but it exists, or "replace" but it doesn't.
     */
    @Transactional(readOnly = true)
    public UploadTarget resolveTarget(User uploader, UploadRequest request) {
        String ownDepartment = departmentRepository.findById(uploader.getDepartmentId())
                .map(Department::getName)
                .orElseThrow(() -> new IllegalStateException("user " + uploader.getUserId() + " has no valid department"));
        String department = request.scope() == Scope.ALL ? UploadPolicy.ALL_DEPARTMENTS : ownDepartment;

        if (request.campaignId() != null) {
            Campaign campaign = campaignRepository.findById(request.campaignId())
                    .orElseThrow(() -> error(HttpStatus.BAD_REQUEST, "unknown campaignId: " + request.campaignId()));
            if (!campaign.getDepartmentId().equals(uploader.getDepartmentId())) {
                throw error(HttpStatus.FORBIDDEN, "campaign " + request.campaignId() + " belongs to another department");
            }
        }

        boolean replacing = request.mode() == UploadMode.REPLACE;
        UUID replacesUploadId = request.contentKind() == ContentKind.DOCUMENT
                ? checkDocument(request, department, ownDepartment)
                : checkDataTable(request);
        return new UploadTarget(department, replacesUploadId, UploadPolicy.needsApproval(replacing, department));
    }

    /** The version a document upload replaces (null for a new one), or an error if the mode doesn't fit what exists. */
    private UUID checkDocument(UploadRequest request, String department, String ownDepartment) {
        if (request.mode() == UploadMode.NEW) {
            boolean exists = request.docType() == DocType.SOP
                    ? documentRepository.findSop(department, request.title()).isPresent()
                    : documentRepository.findByCampaignIdAndDocType(request.campaignId(), request.docType().value()).isPresent();
            if (exists) {
                throw error(HttpStatus.CONFLICT, "this document already exists; upload it with mode=replace");
            }
            return null;
        }

        // The uploader names the document to replace, so a new version can also change its title
        Document document = documentRepository.findById(request.replacesDocId())
                .orElseThrow(() -> error(HttpStatus.CONFLICT, "document " + request.replacesDocId() + " does not exist; nothing to replace"));
        if (!document.getDepartment().equals(ownDepartment) && !document.getDepartment().equals(UploadPolicy.ALL_DEPARTMENTS)) {
            throw error(HttpStatus.FORBIDDEN, "document " + document.getDocId() + " belongs to another department");
        }
        if (!document.getDocType().equals(request.docType().value())) {
            throw error(HttpStatus.BAD_REQUEST, "document " + document.getDocId() + " is a " + document.getDocType()
                    + ", not a " + request.docType().value());
        }
        if (!Objects.equals(document.getCampaignId(), request.campaignId())) {
            throw error(HttpStatus.BAD_REQUEST, "document " + document.getDocId() + " belongs to campaign " + document.getCampaignId());
        }
        if (request.docType() == DocType.SOP) {
            documentRepository.findSop(department, request.title())
                    .filter(other -> !other.getDocId().equals(document.getDocId()))
                    .ifPresent(other -> {
                        throw error(HttpStatus.CONFLICT, "another SOP already has this title: document " + other.getDocId());
                    });
        }
        return document.getUploadId();
    }

    private UUID checkDataTable(UploadRequest request) {
        boolean hasRows = dataTableRepository.hasRows(request.tableType(), request.campaignId());
        if (request.mode() == UploadMode.NEW) {
            if (hasRows) {
                throw error(HttpStatus.CONFLICT, "campaign " + request.campaignId() + " already has "
                        + request.tableType().value() + " data; upload it with mode=replace");
            }
            return null;
        }
        if (!hasRows) {
            throw error(HttpStatus.CONFLICT, "campaign " + request.campaignId() + " has no "
                    + request.tableType().value() + " data; nothing to replace");
        }
        return currentTableVersion(request.campaignId(), request.tableType().value());
    }

    /** The upload behind the rows currently in a table; null if they came from elsewhere (the mock data). */
    private UUID currentTableVersion(Long campaignId, String tableType) {
        return uploadRepository.findFirstByCampaignIdAndTableTypeAndStatusOrderByUpdatedAtDesc(campaignId, tableType, Upload.DONE)
                .map(Upload::getUploadId)
                .orElse(null);
    }

    // ------------------------------------------------------------------ recording

    /** The upload row, and unless it waits for approval, its event in the outbox: one transaction. */
    @Transactional
    public void record(Upload upload) {
        uploadRepository.save(upload);
        if (!upload.isPendingApproval()) {
            enqueueEvent(upload);
        }
    }

    private void enqueueEvent(Upload upload) {
        UploadEvent event = new UploadEvent(
                upload.getUploadId().toString(), upload.getStorageKey(), ContentKind.fromValue(upload.getContentKind()),
                upload.getDocType() == null ? null : DocType.fromValue(upload.getDocType()),
                upload.getTableType() == null ? null : TableType.fromValue(upload.getTableType()),
                upload.getDepartment(), upload.getCampaignId(), upload.getCategory(), Source.fromValue(upload.getSource()));
        // Serialized here, inside the transaction: if it fails, nothing is written. JacksonException is
        // unchecked, so it simply rolls the transaction back.
        // The ObjectMapper is tools.jackson.*, not the com.fasterxml.jackson.* of most tutorials: Spring
        // Boot 4 switched to Jackson 3. Annotations such as @JsonValue stayed in
        // com.fasterxml.jackson.annotation.*, so mixing the two package names is expected
        String payload = objectMapper.writeValueAsString(event);
        // uploadId as the key: messages of one upload always land in the same partition, so if an upload
        // ever produces several messages (say, chunking progress events) their order holds
        outboxRepository.save(new OutboxMessage(uploadTopic, event.uploadId(), payload));
    }

    // ------------------------------------------------------------------ approval

    /** What this user has to decide. Only managers and the executive review anything. */
    @Transactional(readOnly = true)
    public List<Upload> pendingFor(User reviewer) {
        return switch (reviewer.getRole()) {
            case UploadPolicy.MANAGER -> uploadRepository.findPendingFromStaffOf(reviewer.getDepartmentId());
            case UploadPolicy.EXECUTIVE -> uploadRepository.findPendingFromManagers();
            default -> throw error(HttpStatus.FORBIDDEN, "only managers and the executive review uploads");
        };
    }

    /**
     * Approves a waiting upload: it becomes received and its event goes into the outbox, in one transaction.
     * Until this moment the old version stayed in effect.
     *
     * Optimistic concurrency: a request can wait for days, and locking the document all that time is out of the
     * question. Instead the version it wants to replace is checked now. If that is no longer the current one,
     * someone else replaced it meanwhile, and approving would overwrite their work: the request is void.
     * The caller gets the row back with status rejected in that case; the transaction still commits, so the
     * decision is on record.
     */
    @Transactional
    public Upload approve(UUID uploadId, User reviewer, String comment) {
        Upload upload = lockForReview(uploadId, reviewer);
        if (!stillApplies(upload)) {
            upload.review(Upload.REJECTED, reviewer.getUserId(), comment,
                    "superseded: the document changed while this upload was waiting for approval");
            return uploadRepository.save(upload);
        }
        upload.review(Upload.RECEIVED, reviewer.getUserId(), comment, null);
        uploadRepository.save(upload);
        enqueueEvent(upload);
        return upload;
    }

    /** Rejects a waiting upload. Nothing was published, so the current version is simply untouched. */
    @Transactional
    public Upload reject(UUID uploadId, User reviewer, String comment) {
        Upload upload = lockForReview(uploadId, reviewer);
        upload.review(Upload.REJECTED, reviewer.getUserId(), comment, null);
        return uploadRepository.save(upload);
    }

    private Upload lockForReview(UUID uploadId, User reviewer) {
        Upload upload = uploadRepository.lockById(uploadId)
                .orElseThrow(() -> error(HttpStatus.NOT_FOUND, "unknown upload: " + uploadId));
        if (!UploadPolicy.canReview(reviewer, uploaderOf(upload))) {
            throw error(HttpStatus.FORBIDDEN, "only the uploader's superior can review this upload");
        }
        if (!upload.isPendingApproval()) {
            throw error(HttpStatus.CONFLICT, "upload " + uploadId + " is " + upload.getStatus() + ", not waiting for approval");
        }
        return upload;
    }

    private boolean stillApplies(Upload upload) {
        if (ContentKind.DATA_TABLE.value().equals(upload.getContentKind())) {
            return Objects.equals(upload.getReplacesUploadId(),
                    currentTableVersion(upload.getCampaignId(), upload.getTableType()));
        }
        if (upload.getReplacesUploadId() != null) {
            return documentRepository.existsByUploadId(upload.getReplacesUploadId());
        }
        // A new ALL document: nobody may have created the same one while it waited
        return documentRepository.findSop(upload.getDepartment(), upload.getTitle()).isEmpty();
    }

    // ------------------------------------------------------------------ reconciliation

    /** What republishIfStuck did. */
    public enum Republish { REPUBLISHED, GAVE_UP, NOTHING_TO_DO }

    /**
     * Publishes an upload's event again, once the reconciliation job has concluded that its message is gone. Checks
     * again under the row lock, because the upload may have moved on since the job looked, and a second backend
     * instance may be doing the same. Publishing twice would be safe anyway, as the consumer is idempotent. Stops
     * at the limit: an upload that keeps getting stuck needs a person, and failed with a reason gets one's attention.
     */
    @Transactional
    public Republish republishIfStuck(UUID uploadId, Instant cutoff, int maxRepublishes) {
        Upload upload = uploadRepository.lockById(uploadId).orElse(null);
        if (upload == null || !upload.isOnItsWay() || !upload.lastActivity().isBefore(cutoff)) {
            return Republish.NOTHING_TO_DO;
        }
        if (upload.getRepublishCount() >= maxRepublishes) {
            upload.changeStatus(Upload.FAILED, "not finished after being published again " + maxRepublishes
                    + " times; was " + upload.getStatus() + " with no message left in Kafka");
            uploadRepository.save(upload);
            return Republish.GAVE_UP;
        }
        upload.recordRepublish(Instant.now());
        uploadRepository.save(upload);
        enqueueEvent(upload);
        return Republish.REPUBLISHED;
    }

    // ------------------------------------------------------------------ status

    @Transactional(readOnly = true)
    public Upload statusFor(UUID uploadId, User viewer) {
        Upload upload = uploadRepository.findById(uploadId)
                .orElseThrow(() -> error(HttpStatus.NOT_FOUND, "unknown upload: " + uploadId));
        // An automatic sync has no uploader: only admin can see it
        boolean visible = UploadPolicy.ADMIN.equals(viewer.getRole())
                || (upload.getUploadedBy() != null && UploadPolicy.canSeeStatus(viewer, uploaderOf(upload)));
        if (!visible) {
            throw error(HttpStatus.FORBIDDEN, "not your upload");
        }
        return upload;
    }

    private User uploaderOf(Upload upload) {
        return userRepository.findById(upload.getUploadedBy())
                .orElseThrow(() -> new IllegalStateException("upload " + upload.getUploadId() + " has no valid uploader"));
    }

    private static ResponseStatusException error(HttpStatus status, String reason) {
        return new ResponseStatusException(status, reason);
    }
}
