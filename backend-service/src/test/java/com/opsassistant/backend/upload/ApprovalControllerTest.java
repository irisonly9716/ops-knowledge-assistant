package com.opsassistant.backend.upload;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.opsassistant.backend.document.Document;
import java.nio.file.Files;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.test.web.servlet.MvcResult;

/**
 * The approval queue, through the real UploadService. A replacement waits for the uploader's superior, the old
 * version stays in effect meanwhile, and approving a request that went stale must not overwrite newer work.
 */
@WebMvcTest(ApprovalController.class)
class ApprovalControllerTest extends UploadWebTestSupport {

    private static final long STAFF_ID = 5;
    private static final long MANAGER_ID = 6;
    private static final UUID CURRENT_VERSION = UUID.randomUUID();

    private String storageKey;

    @BeforeEach
    void aFileIsWaiting() throws Exception {
        storageKey = UUID.randomUUID() + "-note.md";
        Files.writeString(uploadDir.resolve(storageKey), "# New version");
    }

    // ------------------------------------------------------------------ the queue

    @Test
    void a_manager_sees_the_waiting_uploads_of_their_own_staff() throws Exception {
        Upload upload = waiting(STAFF_ID);
        when(uploadRepository.findPendingFromStaffOf(1L)).thenReturn(List.of(upload));

        mockMvc.perform(get("/documents/approvals").header("X-User-Id", MANAGER))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].uploadId").value(upload.getUploadId().toString()))
                .andExpect(jsonPath("$[0].status").value("pending_approval"))
                .andExpect(jsonPath("$.length()").value(1));
    }

    @Test
    void a_manager_of_another_department_gets_their_own_queue() throws Exception {
        Upload upload = waiting(STAFF_ID);
        when(uploadRepository.findPendingFromStaffOf(1L)).thenReturn(List.of(upload));

        mockMvc.perform(get("/documents/approvals").header("X-User-Id", GROCERY_MANAGER))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.length()").value(0));
    }

    @Test
    void the_executive_sees_the_waiting_uploads_of_managers() throws Exception {
        Upload upload = waiting(MANAGER_ID);
        when(uploadRepository.findPendingFromManagers()).thenReturn(List.of(upload));

        mockMvc.perform(get("/documents/approvals").header("X-User-Id", EXECUTIVE))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].uploadId").value(upload.getUploadId().toString()));
    }

    @ParameterizedTest
    @CsvSource({STAFF, ADMIN})
    void staff_and_admin_have_no_queue(String user) throws Exception {
        mockMvc.perform(get("/documents/approvals").header("X-User-Id", user)).andExpect(status().isForbidden());
    }

    @Test
    void the_queue_needs_a_known_user() throws Exception {
        mockMvc.perform(get("/documents/approvals")).andExpect(status().isUnauthorized());
    }

    // ------------------------------------------------------------------ approving

    @Test
    void approving_publishes_the_upload_and_records_the_decision() throws Exception {
        Upload upload = replacingDocument(STAFF_ID);
        when(documentRepository.existsByUploadId(CURRENT_VERSION)).thenReturn(true);

        mockMvc.perform(post("/documents/approvals/{id}/approve", upload.getUploadId()).header("X-User-Id", MANAGER)
                        .param("comment", "looks right"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("received"))
                .andExpect(jsonPath("$.reviewedBy").value(6))
                .andExpect(jsonPath("$.reviewComment").value("looks right"));

        assertThat(upload.getReviewedAt()).isNotNull();
        assertThat(event())
                .containsEntry("uploadId", upload.getUploadId().toString())
                .containsEntry("storageKey", storageKey)
                .containsEntry("docType", "sop")
                .containsEntry("department", "Beauty");
        assertThat(uploadDir.resolve(storageKey)).exists();   // the consumer still has to read it
    }

    @Test
    void the_executive_approves_a_manager() throws Exception {
        Upload upload = replacingDocument(MANAGER_ID);
        when(documentRepository.existsByUploadId(CURRENT_VERSION)).thenReturn(true);

        mockMvc.perform(post("/documents/approvals/{id}/approve", upload.getUploadId()).header("X-User-Id", EXECUTIVE))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("received"));
    }

    /**
     * Optimistic concurrency: the request waited, and meanwhile someone else's new version went live. Approving it
     * would overwrite their work with a file based on the old version, so the request is void, on the record, and
     * its file is gone.
     */
    @Test
    void approving_when_the_document_changed_meanwhile_is_a_conflict_and_voids_the_request() throws Exception {
        Upload upload = replacingDocument(STAFF_ID);
        when(documentRepository.existsByUploadId(CURRENT_VERSION)).thenReturn(false);

        MvcResult result = mockMvc.perform(post("/documents/approvals/{id}/approve", upload.getUploadId()).header("X-User-Id", MANAGER))
                .andExpect(status().isConflict())
                .andReturn();

        assertThat(errorOf(result)).contains("superseded");
        assertThat(upload.getStatus()).isEqualTo("rejected");
        assertThat(upload.getReviewedBy()).isEqualTo(MANAGER_ID);
        verify(uploadRepository).save(upload);
        verify(outboxRepository, never()).save(any());
        assertThat(uploadDir.resolve(storageKey)).doesNotExist();
    }

    @Test
    void approving_a_table_replacement_compares_with_the_upload_behind_the_current_rows() throws Exception {
        Upload upload = waiting(STAFF_ID);
        tableReplacement(upload);
        Upload current = new Upload();
        current.setUploadId(CURRENT_VERSION);
        when(uploadRepository.findFirstByCampaignIdAndTableTypeAndStatusOrderByUpdatedAtDesc(BEAUTY_CAMPAIGN, "campaign_products", "done"))
                .thenReturn(Optional.of(current));

        mockMvc.perform(post("/documents/approvals/{id}/approve", upload.getUploadId()).header("X-User-Id", MANAGER))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("received"));

        assertThat(event()).containsEntry("tableType", "campaign_products").containsEntry("docType", null);
    }

    @Test
    void approving_a_table_replacement_when_a_newer_upload_finished_meanwhile_is_a_conflict() throws Exception {
        Upload upload = waiting(STAFF_ID);
        tableReplacement(upload);
        Upload newer = new Upload();
        newer.setUploadId(UUID.randomUUID());
        when(uploadRepository.findFirstByCampaignIdAndTableTypeAndStatusOrderByUpdatedAtDesc(BEAUTY_CAMPAIGN, "campaign_products", "done"))
                .thenReturn(Optional.of(newer));

        mockMvc.perform(post("/documents/approvals/{id}/approve", upload.getUploadId()).header("X-User-Id", MANAGER))
                .andExpect(status().isConflict());

        verify(outboxRepository, never()).save(any());
    }

    @Test
    void approving_a_new_document_for_all_when_the_same_one_appeared_meanwhile_is_a_conflict() throws Exception {
        Upload upload = waiting(STAFF_ID);
        upload.setDepartment("ALL");
        upload.setTitle("Inventory Check");
        when(documentRepository.findSop("ALL", "Inventory Check"))
                .thenReturn(Optional.of(new Document(42L, "sop", "Inventory Check", "ALL", null, UUID.randomUUID(), 1)));

        mockMvc.perform(post("/documents/approvals/{id}/approve", upload.getUploadId()).header("X-User-Id", MANAGER))
                .andExpect(status().isConflict());

        verify(outboxRepository, never()).save(any());
    }

    @Test
    void approving_a_new_document_for_all_publishes_it() throws Exception {
        Upload upload = waiting(STAFF_ID);
        upload.setDepartment("ALL");
        upload.setTitle("Inventory Check");

        mockMvc.perform(post("/documents/approvals/{id}/approve", upload.getUploadId()).header("X-User-Id", MANAGER))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("received"));

        assertThat(event()).containsEntry("department", "ALL");
    }

    // ------------------------------------------------------------------ rejecting

    @Test
    void rejecting_publishes_nothing_removes_the_file_and_keeps_the_reason() throws Exception {
        Upload upload = replacingDocument(STAFF_ID);

        mockMvc.perform(post("/documents/approvals/{id}/reject", upload.getUploadId()).header("X-User-Id", MANAGER)
                        .param("comment", "wrong template"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("rejected"))
                .andExpect(jsonPath("$.reviewComment").value("wrong template"));

        assertThat(upload.getReviewedBy()).isEqualTo(MANAGER_ID);
        verify(outboxRepository, never()).save(any());
        assertThat(uploadDir.resolve(storageKey)).doesNotExist();
    }

    // ------------------------------------------------------------------ who may decide

    @ParameterizedTest
    @CsvSource({
            STAFF + ",           approve",   // the uploader themselves
            OTHER_STAFF + ",     approve",
            GROCERY_MANAGER + ", approve",   // can't see department-only content
            EXECUTIVE + ",       approve",   // reviews managers, not staff
            ADMIN + ",           approve",   // keeps the system, not the content
            STAFF + ",           reject",
            GROCERY_MANAGER + ", reject",
            ADMIN + ",           reject",
    })
    void only_the_uploaders_superior_decides(String user, String decision) throws Exception {
        Upload upload = replacingDocument(STAFF_ID);

        mockMvc.perform(post("/documents/approvals/{id}/{decision}", upload.getUploadId(), decision).header("X-User-Id", user))
                .andExpect(status().isForbidden());

        assertUntouched(upload);
    }

    @Test
    void a_manager_can_not_approve_their_own_upload() throws Exception {
        Upload upload = replacingDocument(MANAGER_ID);

        mockMvc.perform(post("/documents/approvals/{id}/approve", upload.getUploadId()).header("X-User-Id", MANAGER))
                .andExpect(status().isForbidden());

        assertUntouched(upload);
    }

    @ParameterizedTest
    @CsvSource({"approve", "reject"})
    void an_upload_that_is_not_waiting_is_a_conflict(String decision) throws Exception {
        Upload upload = replacingDocument(STAFF_ID);
        upload.changeStatus("done", null);

        mockMvc.perform(post("/documents/approvals/{id}/{decision}", upload.getUploadId(), decision).header("X-User-Id", MANAGER))
                .andExpect(status().isConflict());

        verify(uploadRepository, never()).save(any());
        assertThat(uploadDir.resolve(storageKey)).exists();
    }

    @ParameterizedTest
    @CsvSource({"approve", "reject"})
    void an_unknown_upload_is_404(String decision) throws Exception {
        mockMvc.perform(post("/documents/approvals/{id}/{decision}", UUID.randomUUID(), decision).header("X-User-Id", MANAGER))
                .andExpect(status().isNotFound());
    }

    @Test
    void a_decision_needs_a_known_user() throws Exception {
        mockMvc.perform(post("/documents/approvals/{id}/approve", UUID.randomUUID())).andExpect(status().isUnauthorized());
    }

    // ------------------------------------------------------------------ helpers

    private void assertUntouched(Upload upload) {
        assertThat(upload.getStatus()).isEqualTo("pending_approval");
        assertThat(upload.getReviewedBy()).isNull();
        verify(uploadRepository, never()).save(any());
        verify(outboxRepository, never()).save(any());
        assertThat(uploadDir.resolve(storageKey)).exists();
    }

    private Upload replacingDocument(long uploaderId) {
        Upload upload = waiting(uploaderId);
        upload.setReplacesUploadId(CURRENT_VERSION);
        return upload;
    }

    private void tableReplacement(Upload upload) {
        upload.setContentKind("data_table");
        upload.setDocType(null);
        upload.setTableType("campaign_products");
        upload.setCampaignId(BEAUTY_CAMPAIGN);
        upload.setTitle(null);
        upload.setReplacesUploadId(CURRENT_VERSION);
    }

    private Upload waiting(long uploaderId) {
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
        upload.changeStatus(Upload.PENDING_APPROVAL, null);
        when(uploadRepository.lockById(upload.getUploadId())).thenReturn(Optional.of(upload));
        return upload;
    }
}
