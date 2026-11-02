package com.opsassistant.backend.upload;

import java.io.IOException;
import java.time.Instant;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.function.Supplier;
import com.opsassistant.backend.user.CurrentUser;
import com.opsassistant.backend.user.User;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.server.ResponseStatusException;

/**
 * Upload endpoint and upload status. Implements the "two ingestion pipelines share one topic" design in
 * docs/architecture.md: documents (SOP / plan / review) and structured tables (products / customer
 * behavior) both come through this endpoint, and the contentKind field tells the llm-service consumer
 * how to handle them.
 *
 * The rules are in docs/permission-model.md. The uploader comes from @CurrentUser and the department follows
 * from the uploader, so a client can't upload into a department by naming it. The client states whether this
 * is a new document or a replacement, and the system checks that against what exists instead of guessing.
 *
 * The request is accepted once the file is stored and the upload is recorded (UploadService). It never talks
 * to Kafka: OutboxRelay publishes the event afterwards, so a Kafka outage delays processing instead of failing
 * the upload.
 *
 * The request parameters arrive as raw strings -- HTTP form fields can only be strings, so the type system
 * can't help there -- but they are converted to enums as soon as the method starts (parseOrBadRequest), and
 * the rest of the logic only ever sees enums.
 */
@Slf4j
@RestController
@RequestMapping("/documents")
@RequiredArgsConstructor
public class UploadController {

    private static final Set<String> DOCUMENT_EXTENSIONS = Set.of(".md", ".markdown", ".docx", ".pdf");
    private static final Set<String> TABLE_EXTENSIONS = Set.of(".csv");

    private final UploadService uploadService;
    private final UploadStorage storage;

    @PostMapping("/upload")
    public ResponseEntity<Map<String, String>> upload(
            @CurrentUser User uploader,
            @RequestParam("file") MultipartFile file,
            @RequestParam("contentKind") String contentKindRaw,
            @RequestParam(value = "mode", required = false) String modeRaw,
            @RequestParam(value = "docType", required = false) String docTypeRaw,
            @RequestParam(value = "tableType", required = false) String tableTypeRaw,
            @RequestParam(value = "campaignId", required = false) Long campaignId,
            @RequestParam(value = "title", required = false) String title,
            @RequestParam(value = "scope", required = false) String scopeRaw,
            @RequestParam(value = "replacesDocId", required = false) Long replacesDocId,
            @RequestParam(value = "category", required = false) String category
    ) throws IOException {
        if (!UploadPolicy.canUpload(uploader)) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN,
                    uploader.getRole() + " can't upload; documents and data tables are uploaded by their departments");
        }
        ContentKind contentKind = parseOrBadRequest(() -> ContentKind.fromValue(contentKindRaw));
        UploadMode mode = parseOrBadRequest(() -> UploadMode.fromValue(modeRaw));
        DocType docType = null;
        TableType tableType = null;

        switch (contentKind) {
            case DOCUMENT -> {
                docType = parseOrBadRequest(() -> DocType.fromValue(docTypeRaw));
                if (docType.needsCampaign() && campaignId == null) {
                    throw badRequest("docType=" + docType.value() + " requires a campaignId");
                }
                if (!docType.needsCampaign() && campaignId != null) {
                    throw badRequest("docType=sop must not have a campaignId");
                }
                if (mode == UploadMode.REPLACE && replacesDocId == null) {
                    throw badRequest("mode=replace requires replacesDocId, the document to replace");
                }
            }
            case DATA_TABLE -> {
                tableType = parseOrBadRequest(() -> TableType.fromValue(tableTypeRaw));
                if (campaignId == null) {
                    throw badRequest("contentKind=data_table requires a campaignId");
                }
            }
        }
        if (replacesDocId != null && (mode == UploadMode.NEW || contentKind == ContentKind.DATA_TABLE)) {
            throw badRequest("replacesDocId is only for replacing a document");
        }
        requireAllowedFileType(contentKind, file.getOriginalFilename());

        // An SOP is identified by its department + title (the natural key in documents) and says who it is
        // for. Plan and review titles are generated from the campaign, and they belong to its department.
        String cleanTitle = title == null || title.isBlank() ? null : title.strip();
        Scope scope = null;
        if (docType == DocType.SOP) {
            if (cleanTitle == null) {
                throw badRequest("docType=sop requires a title");
            }
            scope = parseOrBadRequest(() -> Scope.fromValue(scopeRaw));
        } else if (cleanTitle != null || scopeRaw != null) {
            throw badRequest("title and scope are only for sop; campaign documents take them from the campaign");
        }

        UploadRequest request = new UploadRequest(contentKind, docType, tableType, mode, campaignId, cleanTitle, scope,
                replacesDocId, category);
        UploadTarget target = uploadService.resolveTarget(uploader, request);

        UUID uploadId = UUID.randomUUID();
        String storageKey = storage.save(file, uploadId);
        Upload upload = newUpload(uploadId, storageKey, uploader, request, target, file.getOriginalFilename());
        try {
            uploadService.record(upload);
        } catch (RuntimeException e) {
            // Nothing was committed (the upload and its event are one transaction), so the stored file
            // belongs to nobody
            storage.deleteQuietly(storageKey);
            throw new ResponseStatusException(HttpStatus.INTERNAL_SERVER_ERROR,
                    "failed to record the upload; it did not take effect", e);
        }

        log.info("upload accepted uploadId={} contentKind={} status={}", uploadId, contentKind.value(), upload.getStatus());
        return ResponseEntity.accepted().body(Map.of("uploadId", uploadId.toString(), "status", upload.getStatus()));
    }

    /** How far an upload got. Visible to its uploader, whoever may review it, and admin. */
    @GetMapping("/{uploadId}/status")
    public UploadStatusResponse status(@CurrentUser User viewer, @PathVariable UUID uploadId) {
        return UploadStatusResponse.of(uploadService.statusFor(uploadId, viewer));
    }

    /**
     * Each enum's fromValue() throws IllegalArgumentException when nothing matches (see
     * ContentKind and the others). This catches it in one place and turns it into a
     * ResponseStatusException with status 400, so the call sites don't each need their own try/catch.
     *
     * Supplier<T> is a Java functional interface: "an action that takes no arguments and returns
     * a T". The lambda () -> ContentKind.fromValue(contentKindRaw) is such an action; it only runs
     * when parser.get() is called, which is what lets the whole throwing call be wrapped and passed in.
     */
    private static <T> T parseOrBadRequest(Supplier<T> parser) {
        try {
            return parser.get();
        } catch (IllegalArgumentException e) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, e.getMessage());
        }
    }

    private static ResponseStatusException badRequest(String message) {
        return new ResponseStatusException(HttpStatus.BAD_REQUEST, message);
    }

    /**
     * Rejected right here, not after a trip through Kafka to the consumer's parser and the dead-letter topic.
     * The extension only says what the file claims to be; a broken file still fails in the consumer.
     */
    private static void requireAllowedFileType(ContentKind contentKind, String filename) {
        Set<String> allowed = contentKind == ContentKind.DOCUMENT ? DOCUMENT_EXTENSIONS : TABLE_EXTENSIONS;
        String name = filename == null ? "" : filename.toLowerCase(Locale.ROOT);
        int dot = name.lastIndexOf('.');
        if (dot < 0 || !allowed.contains(name.substring(dot))) {
            throw badRequest("contentKind=" + contentKind.value() + " accepts " + allowed.stream().sorted().toList()
                    + ", got: " + filename);
        }
    }

    private static Upload newUpload(UUID uploadId, String storageKey, User uploader, UploadRequest request,
                                    UploadTarget target, String originalFilename) {
        Upload upload = new Upload();
        upload.setUploadId(uploadId);
        upload.setSource(Source.MANUAL.value());
        upload.setUploadedBy(uploader.getUserId());
        upload.setContentKind(request.contentKind().value());
        upload.setDocType(request.docType() == null ? null : request.docType().value());
        upload.setTableType(request.tableType() == null ? null : request.tableType().value());
        upload.setDepartment(target.department());
        upload.setCampaignId(request.campaignId());
        upload.setTitle(request.title());
        upload.setCategory(request.category());
        upload.setOriginalFilename(originalFilename == null ? storageKey : originalFilename);
        upload.setStorageKey(storageKey);
        upload.setReplacesUploadId(target.replacesUploadId());
        upload.setCreatedAt(Instant.now());
        upload.changeStatus(target.needsApproval() ? Upload.PENDING_APPROVAL : Upload.RECEIVED, null);
        return upload;
    }
}
