package com.opsassistant.backend.upload;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.Map;
import java.util.UUID;
import java.util.function.Supplier;
import com.opsassistant.backend.campaign.CampaignRepository;
import com.opsassistant.backend.department.DepartmentRepository;
import com.opsassistant.backend.user.User;
import com.opsassistant.backend.user.UserRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.server.ResponseStatusException;

/**
 * Upload endpoint. Implements the "two ingestion pipelines share one topic" design in
 * docs/architecture.md: documents (SOP / plan / review) and structured tables (products / customer
 * behavior) both come through this endpoint, and the contentKind field tells the llm-service consumer
 * how to handle them.
 *
 * The request is accepted once the file is stored and the upload is recorded together with its event
 * (UploadService, one transaction). It never talks to Kafka: OutboxRelay publishes the event afterwards,
 * so a Kafka outage delays processing instead of failing the upload.
 *
 * The request parameters (contentKind/docType/tableType) arrive as raw strings -- HTTP form
 * fields can only be strings, so the type system can't help there -- but they are converted to
 * enums as soon as the method starts (parseOrBadRequest), and the rest of the logic only ever
 * sees enums.
 */
@Slf4j
@RestController
@RequestMapping("/documents")
@RequiredArgsConstructor
public class UploadController {

    private static final String ALL_DEPARTMENTS = "ALL"; // sentinel for sop documents, not a row in departments

    private final DepartmentRepository departmentRepository;
    private final CampaignRepository campaignRepository;
    private final UserRepository userRepository;
    private final UploadService uploadService;

    @Value("${document.upload-dir}")
    private String uploadDir;

    @PostMapping("/upload")
    public ResponseEntity<Map<String, String>> upload(
            @RequestHeader(value = "X-User-Id", required = false) String userIdHeader,
            @RequestParam("file") MultipartFile file,
            @RequestParam("contentKind") String contentKindRaw,
            @RequestParam(value = "docType", required = false) String docTypeRaw,
            @RequestParam(value = "tableType", required = false) String tableTypeRaw,
            @RequestParam("department") String department,
            @RequestParam(value = "campaignId", required = false) Long campaignId,
            @RequestParam(value = "title", required = false) String title,
            @RequestParam(value = "category", required = false) String category
    ) throws IOException {
        // Who is uploading comes first: an unknown caller gets 401 before any parameter is looked at
        User uploader = resolveUser(userIdHeader);
        ContentKind contentKind = parseOrBadRequest(() -> ContentKind.fromValue(contentKindRaw));
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
            }
            case DATA_TABLE -> {
                tableType = parseOrBadRequest(() -> TableType.fromValue(tableTypeRaw));
                if (campaignId == null) {
                    throw badRequest("contentKind=data_table requires a campaignId");
                }
            }
        }

        // An SOP is identified by its department + title (the natural key in documents), so it must have
        // one. Plan and review titles are generated from the campaign, so a title there is a mistake.
        String cleanTitle = title == null || title.isBlank() ? null : title.strip();
        if (docType == DocType.SOP && cleanTitle == null) {
            throw badRequest("docType=sop requires a title");
        }
        if (docType != DocType.SOP && cleanTitle != null) {
            throw badRequest("title is only for sop; campaign document titles are generated from the campaign");
        }

        // department / campaignId are not a fixed set of values baked into the schema like
        // doc_type; they are business data (a new department or campaign can appear any day),
        // so no enum. They still have to be checked against the real tables.
        // "ALL" is the sop-only sentinel and not a row in departments, so it is let through.
        if (!ALL_DEPARTMENTS.equals(department) && !departmentRepository.existsByName(department)) {
            throw badRequest("unknown department: " + department);
        }
        if (campaignId != null && !campaignRepository.existsById(campaignId)) {
            throw badRequest("unknown campaignId: " + campaignId);
        }

        String uploadId = UUID.randomUUID().toString();
        Path dest = saveFile(file, uploadId);

        UploadEvent event = new UploadEvent(
                uploadId, dest.getFileName().toString(), contentKind, docType, tableType,
                department, campaignId, category, Source.MANUAL
        );
        Upload upload = newUpload(event, uploader, cleanTitle, file.getOriginalFilename());
        try {
            uploadService.record(upload, event);
        } catch (RuntimeException e) {
            // Nothing was committed (the upload and its event are one transaction), so the stored file
            // belongs to nobody
            deleteQuietly(dest);
            throw new ResponseStatusException(HttpStatus.INTERNAL_SERVER_ERROR,
                    "failed to record the upload; it did not take effect", e);
        }

        log.info("upload accepted uploadId={} contentKind={}; its event is in the outbox", uploadId, contentKind.value());
        return ResponseEntity.accepted().body(Map.of("uploadId", uploadId, "status", "accepted"));
    }

    /**
     * Each enum's fromValue() throws IllegalArgumentException when nothing matches (see
     * ContentKind and the others). This catches it in one place and turns it into a
     * ResponseStatusException with status 400, so the three call sites
     * (contentKind/docType/tableType) don't each need their own try/catch.
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

    private ResponseStatusException badRequest(String message) {
        return new ResponseStatusException(HttpStatus.BAD_REQUEST, message);
    }

    /**
     * X-User-Id -> the user. Missing, not a number, or no such user are all 401: the caller is not
     * identified. (The header is simulated identity; real authentication is designed, not built --
     * see docs/permission-model.md and issue #5.)
     */
    private User resolveUser(String userIdHeader) {
        if (userIdHeader == null || userIdHeader.isBlank()) {
            throw new ResponseStatusException(HttpStatus.UNAUTHORIZED, "missing X-User-Id header");
        }
        try {
            return userRepository.findById(Long.valueOf(userIdHeader.strip()))
                    .orElseThrow(() -> new ResponseStatusException(HttpStatus.UNAUTHORIZED, "unknown user: " + userIdHeader));
        } catch (NumberFormatException e) {
            throw new ResponseStatusException(HttpStatus.UNAUTHORIZED, "X-User-Id must be a user id, got: " + userIdHeader);
        }
    }

    /** The uploads row for an accepted upload: status received, until the consumer picks it up. */
    private static Upload newUpload(UploadEvent event, User uploader, String title, String originalFilename) {
        Upload upload = new Upload();
        upload.setUploadId(UUID.fromString(event.uploadId()));
        upload.setSource(event.source().value());
        upload.setUploadedBy(uploader.getUserId());
        upload.setContentKind(event.contentKind().value());
        upload.setDocType(event.docType() == null ? null : event.docType().value());
        upload.setTableType(event.tableType() == null ? null : event.tableType().value());
        upload.setDepartment(event.department());
        upload.setCampaignId(event.campaignId());
        upload.setTitle(title);
        upload.setOriginalFilename(originalFilename == null ? event.storageKey() : originalFilename);
        upload.setStorageKey(event.storageKey());
        upload.setCreatedAt(Instant.now());
        upload.changeStatus(Upload.RECEIVED, null);
        return upload;
    }

    private Path saveFile(MultipartFile file, String uploadId) throws IOException {
        Path dir = Path.of(uploadDir);
        Files.createDirectories(dir);
        // Prefix the uploadId so two people uploading the same file name never overwrite each other
        String filename = uploadId + "-" + sanitizeFilename(file.getOriginalFilename());
        Path dest = dir.resolve(filename);
        file.transferTo(dest);
        return dest;
    }

    /**
     * getOriginalFilename() is WHATEVER THE CLIENT SENT: it can be "../../evil.md", "C:\x\y.md",
     * contain control characters, or be null. Putting it straight into a file name on disk is the
     * classic path traversal hole. Path separators, characters Windows forbids and control
     * characters are all replaced with underscores, giving a string that can only ever be a
     * single-level file name on any operating system. Ordinary characters, including non-ASCII
     * letters, are kept.
     * Note: replace, don't "take the last segment" -- that misses Windows-style backslash paths on Linux.
     */
    static String sanitizeFilename(String original) {
        if (original == null) {
            return "upload";
        }
        String cleaned = original.replaceAll("[\\\\/:*?\"<>|\\p{Cntrl}]", "_").strip();
        if (cleaned.length() > 100) {
            cleaned = cleaned.substring(cleaned.length() - 100); // keep the end so the extension survives
        }
        return cleaned.isBlank() || cleaned.chars().allMatch(c -> c == '.') ? "upload" : cleaned;
    }

    /** Remove an orphan file. If the delete itself fails, only log it; it must not mask the original error. */
    private void deleteQuietly(Path file) {
        try {
            Files.deleteIfExists(file);
        } catch (IOException e) {
            log.warn("failed to remove orphan file, delete it by hand: {}", file, e);
        }
    }

    /**
     * Spring's default error response hides the reason inside a ResponseStatusException and returns
     * a generic "Bad Request" (so internal exception details don't leak to callers).
     * server.error.include-message: always was tried to get the details back and had no effect;
     * instead of guessing configuration keys, this @ExceptionHandler makes the behavior fully
     * explicit, and debugging or frontend integration shows exactly which validation failed.
     */
    @ExceptionHandler(ResponseStatusException.class)
    public ResponseEntity<Map<String, String>> handleBadRequest(ResponseStatusException e) {
        return ResponseEntity.status(e.getStatusCode())
                .body(Map.of("error", String.valueOf(e.getReason())));
    }
}
