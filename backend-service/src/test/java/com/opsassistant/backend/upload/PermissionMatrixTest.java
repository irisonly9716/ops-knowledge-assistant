package com.opsassistant.backend.upload;

import static com.opsassistant.backend.upload.PermissionMatrixTest.Outcome.APPROVED;
import static com.opsassistant.backend.upload.PermissionMatrixTest.Outcome.FORBIDDEN;
import static com.opsassistant.backend.upload.PermissionMatrixTest.Outcome.TAKES_EFFECT;
import static com.opsassistant.backend.upload.PermissionMatrixTest.Outcome.WAITS;
import static java.nio.charset.StandardCharsets.UTF_8;
import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.multipart;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.opsassistant.backend.document.Document;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import java.util.stream.Stream;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.MethodSource;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.test.web.servlet.RequestBuilder;
import org.springframework.test.web.servlet.ResultActions;
import org.springframework.test.web.servlet.request.MockMultipartHttpServletRequestBuilder;

/**
 * The table "What each role can change" of docs/permission-model.md, through the upload and approval endpoints and
 * the real UploadService: each role against each action, approving one's own request and another department's
 * included. The other tests check what each outcome does in detail; this one checks that the table holds, cell by
 * cell, so a change to the rules shows up here as the cell it changed.
 */
@WebMvcTest({UploadController.class, ApprovalController.class})
class PermissionMatrixTest extends UploadWebTestSupport {

    enum Outcome { TAKES_EFFECT, WAITS, APPROVED, FORBIDDEN }

    private static final String CREATE_OWN_DOCUMENT = "create a document for their own department";
    private static final String CREATE_OWN_TABLE = "create a data table for their own department";
    private static final String REPLACE_OWN_DOCUMENT = "replace their own department's document";
    private static final String CREATE_ALL_DOCUMENT = "create a document for every department";
    private static final String REPLACE_ALL_DOCUMENT = "replace a document for every department";
    private static final String REPLACE_OTHER_DOCUMENT = "replace another department's document";
    private static final String CREATE_OTHER_TABLE = "create a data table for another department";
    private static final String DECIDE_STAFF_REQUEST = "approve a Beauty staff member's request";
    private static final String DECIDE_MANAGER_REQUEST = "approve the Beauty manager's request";

    private static final long STAFF_ID = 5;
    private static final long MANAGER_ID = 6;
    private static final long OWN_DOCUMENT = 42;
    private static final long ALL_DOCUMENT = 43;
    private static final long GROCERY_DOCUMENT = 44;
    private static final UUID CURRENT_VERSION = UUID.randomUUID();

    private static final Map<String, String> WHO = Map.of(STAFF, "Beauty staff", OTHER_STAFF, "other Beauty staff",
            MANAGER, "Beauty manager", GROCERY_MANAGER, "Fresh Grocery manager", EXECUTIVE, "executive", ADMIN, "admin");

    record Case(String action, String user, Outcome outcome) {
        @Override
        public String toString() {
            return action + " / " + WHO.get(user) + " -> " + outcome.name().toLowerCase().replace('_', ' ');
        }
    }

    /** One row of the table: who, then what happens, for each of them. */
    private static Stream<Case> row(String action, Object... whoThenOutcome) {
        List<Case> cases = new ArrayList<>();
        for (int i = 0; i < whoThenOutcome.length; i += 2) {
            cases.add(new Case(action, (String) whoThenOutcome[i], (Outcome) whoThenOutcome[i + 1]));
        }
        return cases.stream();
    }

    static Stream<Case> table() {
        return Stream.of(
                row(CREATE_OWN_DOCUMENT, STAFF, TAKES_EFFECT, MANAGER, TAKES_EFFECT, EXECUTIVE, FORBIDDEN, ADMIN, FORBIDDEN),
                row(CREATE_OWN_TABLE, STAFF, TAKES_EFFECT, MANAGER, TAKES_EFFECT, EXECUTIVE, FORBIDDEN, ADMIN, FORBIDDEN),
                row(REPLACE_OWN_DOCUMENT, STAFF, WAITS, MANAGER, WAITS, EXECUTIVE, FORBIDDEN, ADMIN, FORBIDDEN),
                row(CREATE_ALL_DOCUMENT, STAFF, WAITS, MANAGER, WAITS, EXECUTIVE, FORBIDDEN, ADMIN, FORBIDDEN),
                row(REPLACE_ALL_DOCUMENT, STAFF, WAITS, MANAGER, WAITS, EXECUTIVE, FORBIDDEN, ADMIN, FORBIDDEN),
                row(REPLACE_OTHER_DOCUMENT, STAFF, FORBIDDEN, MANAGER, FORBIDDEN, EXECUTIVE, FORBIDDEN, ADMIN, FORBIDDEN),
                row(CREATE_OTHER_TABLE, STAFF, FORBIDDEN, MANAGER, FORBIDDEN, EXECUTIVE, FORBIDDEN, ADMIN, FORBIDDEN),
                // The uploader's superior decides, and only someone who can see the content
                row(DECIDE_STAFF_REQUEST, STAFF, FORBIDDEN, OTHER_STAFF, FORBIDDEN, MANAGER, APPROVED,
                        GROCERY_MANAGER, FORBIDDEN, EXECUTIVE, FORBIDDEN, ADMIN, FORBIDDEN),
                row(DECIDE_MANAGER_REQUEST, STAFF, FORBIDDEN, MANAGER, FORBIDDEN, GROCERY_MANAGER, FORBIDDEN,
                        EXECUTIVE, APPROVED, ADMIN, FORBIDDEN))
                .flatMap(cases -> cases);
    }

    @ParameterizedTest(name = "{0}")
    @MethodSource("table")
    void each_role_can_do_what_the_permission_table_says_and_nothing_else(Case c) throws Exception {
        documents();
        Upload waiting = c.action().startsWith("approve") ? waiting(c.action().equals(DECIDE_STAFF_REQUEST) ? STAFF_ID : MANAGER_ID) : null;

        ResultActions result = mockMvc.perform(request(c, waiting));

        switch (c.outcome()) {
            case TAKES_EFFECT -> {
                result.andExpect(status().isAccepted()).andExpect(jsonPath("$.status").value("received"));
                verify(outboxRepository, times(1)).save(any());
            }
            case WAITS -> {
                result.andExpect(status().isAccepted()).andExpect(jsonPath("$.status").value("pending_approval"));
                verify(outboxRepository, never()).save(any());   // published only once approved
            }
            case APPROVED -> {
                result.andExpect(status().isOk()).andExpect(jsonPath("$.status").value("received"));
                assertThat(waiting.getReviewedBy()).isEqualTo(Long.parseLong(c.user()));
            }
            case FORBIDDEN -> {
                result.andExpect(status().isForbidden());
                verify(uploadRepository, never()).save(any());
                verify(outboxRepository, never()).save(any());
                if (waiting != null) {
                    assertThat(waiting.getStatus()).isEqualTo(Upload.PENDING_APPROVAL);
                } else {
                    assertFileCount(0);
                }
            }
        }
    }

    private RequestBuilder request(Case c, Upload waiting) {
        return switch (c.action()) {
            case CREATE_OWN_DOCUMENT -> upload(c.user(), sop("department"), "note.md");
            case CREATE_OWN_TABLE -> upload(c.user(), table(BEAUTY_CAMPAIGN), "data.csv");
            case REPLACE_OWN_DOCUMENT -> upload(c.user(), replacing(sop("department"), OWN_DOCUMENT), "note.md");
            case CREATE_ALL_DOCUMENT -> upload(c.user(), sop("all"), "note.md");
            case REPLACE_ALL_DOCUMENT -> upload(c.user(), replacing(sop("all"), ALL_DOCUMENT), "note.md");
            case REPLACE_OTHER_DOCUMENT -> upload(c.user(), replacing(sop("department"), GROCERY_DOCUMENT), "note.md");
            case CREATE_OTHER_TABLE -> upload(c.user(), table(GROCERY_CAMPAIGN), "data.csv");
            case DECIDE_STAFF_REQUEST, DECIDE_MANAGER_REQUEST ->
                    post("/documents/approvals/{id}/approve", waiting.getUploadId()).header("X-User-Id", c.user());
            default -> throw new IllegalArgumentException(c.action());
        };
    }

    // ------------------------------------------------------------------ the world each case starts from

    private void documents() {
        document(OWN_DOCUMENT, "Beauty");
        document(ALL_DOCUMENT, "ALL");
        document(GROCERY_DOCUMENT, "Fresh Grocery");
        when(documentRepository.existsByUploadId(CURRENT_VERSION)).thenReturn(true);   // still the current version
    }

    private void document(long id, String department) {
        when(documentRepository.findById(id))
                .thenReturn(Optional.of(new Document(id, "sop", "Inventory Check", department, null, CURRENT_VERSION, 1)));
    }

    /** A Beauty SOP replacement waiting for a decision, uploaded by the given user. */
    private Upload waiting(long uploaderId) throws Exception {
        String storageKey = UUID.randomUUID() + "-note.md";
        Files.writeString(uploadDir.resolve(storageKey), "# New version");
        Upload upload = new Upload();
        upload.setUploadId(UUID.randomUUID());
        upload.setSource("manual");
        upload.setUploadedBy(uploaderId);
        upload.setContentKind("document");
        upload.setDocType("sop");
        upload.setDepartment("Beauty");
        upload.setTitle("Inventory Check");
        upload.setOriginalFilename("note.md");
        upload.setStorageKey(storageKey);
        upload.setReplacesUploadId(CURRENT_VERSION);
        upload.changeStatus(Upload.PENDING_APPROVAL, null);
        when(uploadRepository.lockById(upload.getUploadId())).thenReturn(Optional.of(upload));
        return upload;
    }

    private static Map<String, String> sop(String scope) {
        return new java.util.HashMap<>(Map.of("contentKind", "document", "docType", "sop", "mode", "new",
                "scope", scope, "title", "Inventory Check"));
    }

    private static Map<String, String> table(long campaignId) {
        return Map.of("contentKind", "data_table", "tableType", "campaign_products", "mode", "new",
                "campaignId", String.valueOf(campaignId));
    }

    private static Map<String, String> replacing(Map<String, String> params, long docId) {
        params.put("mode", "replace");
        params.put("replacesDocId", String.valueOf(docId));
        return params;
    }

    private static MockMultipartHttpServletRequestBuilder upload(String user, Map<String, String> params, String filename) {
        MockMultipartHttpServletRequestBuilder builder = multipart("/documents/upload")
                .file(new MockMultipartFile("file", filename, "application/octet-stream", "content".getBytes(UTF_8)));
        builder.header("X-User-Id", user);
        params.forEach(builder::param);
        return builder;
    }
}
