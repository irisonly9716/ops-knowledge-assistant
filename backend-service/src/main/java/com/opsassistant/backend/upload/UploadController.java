package com.opsassistant.backend.upload;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.function.Supplier;
import com.opsassistant.backend.campaign.CampaignRepository;
import com.opsassistant.backend.department.DepartmentRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.support.SendResult;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.server.ResponseStatusException;
import tools.jackson.core.JacksonException;
import tools.jackson.databind.ObjectMapper;

/**
 * Upload endpoint + Kafka producer. Implements the "two ingestion pipelines share one topic"
 * design in docs/architecture.md: documents (SOP / plan / review) and structured tables
 * (products / customer behavior) both come through this endpoint, and the contentKind field
 * tells the llm-service consumer how to handle them.
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

    private final KafkaTemplate<String, String> kafkaTemplate;
    private final ObjectMapper objectMapper; // the Jackson instance Spring Boot auto-configures
    private final DepartmentRepository departmentRepository;
    private final CampaignRepository campaignRepository;

    @Value("${document.upload-topic}")
    private String uploadTopic;

    @Value("${document.upload-dir}")
    private String uploadDir;

    @Value("${document.send-timeout-ms}")
    private long sendTimeoutMs;

    @PostMapping("/upload")
    public ResponseEntity<Map<String, String>> upload(
            @RequestParam("file") MultipartFile file,
            @RequestParam("contentKind") String contentKindRaw,
            @RequestParam(value = "docType", required = false) String docTypeRaw,
            @RequestParam(value = "tableType", required = false) String tableTypeRaw,
            @RequestParam("department") String department,
            @RequestParam(value = "campaignId", required = false) Long campaignId,
            @RequestParam(value = "category", required = false) String category
    ) throws IOException {
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
        sendToKafka(uploadId, event, dest);

        log.info("upload accepted uploadId={} contentKind={} published to topic {}", uploadId, contentKind.value(), uploadTopic);
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

    /**
     * The message is sent only after the file is saved (savedFile), so whether a failed send
     * should delete the file depends on whether the failure is CERTAIN:
     *   certain failure (send() itself throws = the record never entered the send queue; or the
     *   Future completes exceptionally):
     *     the message definitely did not go out, the file is an orphan nobody will claim, delete it.
     *   uncertain outcome (waiting on the Future timed out, the thread was interrupted):
     *     the message may already have gone out, or be about to, and the consumer will come for the
     *     file. Deleting it then creates the worse state "message without file", so keep it and tell
     *     the caller the outcome is unknown.
     * "Timeout != failure" is one of the classic traps of distributed systems. application.yml sets
     * delivery.timeout.ms to 10 seconds while get() here waits 15, so Kafka itself gives a definite
     * answer first and the "uncertain" branch is almost never reached.
     */
    private void sendToKafka(String uploadId, UploadEvent event, Path savedFile) {
        // The package is tools.jackson.*, not the com.fasterxml.jackson.* of most tutorials:
        // Spring Boot 4 switched to Jackson 3 (even the Maven groupId changed). Annotations such as
        // @JsonValue/@JsonCreator stayed in com.fasterxml.jackson.annotation.*, so mixing the two
        // package names is what Jackson 3 really looks like, not a mistake.
        // JacksonException is unchecked; it is caught anyway to turn it into a response with a
        // clear HTTP status instead of an unexplained 500.
        String json;
        try {
            json = objectMapper.writeValueAsString(event);
        } catch (JacksonException e) {
            deleteQuietly(savedFile); // serialization failed before anything was sent = certain failure
            throw new ResponseStatusException(HttpStatus.INTERNAL_SERVER_ERROR, "failed to serialize the message", e);
        }

        // uploadId as the key: messages of one upload always land in the same partition, so if an
        // upload ever produces several messages (say, chunking progress events) their order holds.
        CompletableFuture<SendResult<String, String>> future;
        try {
            future = kafkaTemplate.send(uploadTopic, uploadId, json);
        } catch (RuntimeException e) {
            // send() itself throws (typically: topic missing / broker unreachable, the metadata
            // wait times out), so the record never entered the send queue: certain failure.
            // Watch out: what is thrown here is org.apache.kafka.common.errors.TimeoutException,
            // not java.util.concurrent.TimeoutException. Same simple name, different packages;
            // catching the wrong one lets the exception slip through as Spring's generic 500.
            deleteQuietly(savedFile);
            throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE,
                    "message queue unavailable; the upload did not take effect, please retry later", e);
        }

        try {
            SendResult<String, String> result = future.get(sendTimeoutMs, TimeUnit.MILLISECONDS);
            log.debug("sent to partition={} offset={}",
                    result.getRecordMetadata().partition(), result.getRecordMetadata().offset());
        } catch (ExecutionException e) {
            deleteQuietly(savedFile); // the Future completed exceptionally = certain failure
            throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE,
                    "failed to send the message; the upload did not take effect, please retry later", e);
        } catch (TimeoutException e) {
            log.warn("uploadId={} timed out waiting for the send result; outcome unknown, file kept: {}", uploadId, savedFile);
            throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE,
                    "send outcome unknown; check later before deciding whether to upload again", e);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt(); // restore the interrupt flag; never swallow "you were asked to stop"
            log.warn("uploadId={} interrupted while waiting for the send result; outcome unknown, file kept: {}", uploadId, savedFile);
            throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE, "send interrupted; outcome unknown", e);
        }
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
