package com.opsassistant.backend.upload;

import static java.nio.charset.StandardCharsets.UTF_8;
import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.multipart;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.opsassistant.backend.document.Document;
import java.util.HashMap;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import java.util.stream.Stream;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.junit.jupiter.params.provider.MethodSource;
import org.mockito.ArgumentCaptor;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.test.web.servlet.request.MockMultipartHttpServletRequestBuilder;

/**
 * POST /documents/upload and GET /documents/{id}/status, through the real UploadService.
 *
 * Groups: takes effect at once / waits for approval / refused because of who is asking (403) / refused because
 * of what exists (409) / malformed requests (400) / identity (401) / recording fails / the status endpoint.
 */
@WebMvcTest(UploadController.class)
class UploadControllerTest extends UploadWebTestSupport {

    private static final UUID CURRENT_VERSION = UUID.randomUUID();

    // ------------------------------------------------------------------ takes effect at once

    @Test
    void a_new_sop_for_the_own_department_takes_effect_at_once() throws Exception {
        MvcResult result = mockMvc.perform(request(STAFF, sop())).andExpect(status().isAccepted()).andReturn();

        String uploadId = (String) bodyOf(result).get("uploadId");
        assertThat(bodyOf(result)).containsEntry("status", "received");

        Upload upload = savedUpload();
        assertThat(upload.getUploadId()).hasToString(uploadId);
        assertThat(upload.getStatus()).isEqualTo("received");
        assertThat(upload.getUploadedBy()).isEqualTo(5L);
        assertThat(upload.getDepartment()).isEqualTo("Beauty");
        assertThat(upload.getTitle()).isEqualTo("Inventory Check");
        assertThat(upload.getStorageKey()).isEqualTo(uploadId + "-note.md");
        assertThat(upload.getOriginalFilename()).isEqualTo("note.md");
        assertThat(upload.getReplacesUploadId()).isNull();
        assertThat(uploadDir.resolve(uploadId + "-note.md")).exists();   // the file really was saved

        assertThat(event())
                .containsEntry("uploadId", uploadId)
                .containsEntry("storageKey", uploadId + "-note.md")   // a logical key, not a physical path
                .containsEntry("contentKind", "document")
                .containsEntry("docType", "sop")
                .containsEntry("department", "Beauty")
                .containsEntry("source", "manual");
    }

    @Test
    void the_department_follows_from_the_uploader_and_can_not_be_chosen() throws Exception {
        mockMvc.perform(request(STAFF, sop(), "department", "Fresh Grocery")).andExpect(status().isAccepted());

        assertThat(savedUpload().getDepartment()).isEqualTo("Beauty");
    }

    @Test
    void a_manager_uploads_under_the_same_rule() throws Exception {
        mockMvc.perform(request(MANAGER, sop())).andExpect(status().isAccepted())
                .andExpect(jsonPath("$.status").value("received"));
    }

    @Test
    void a_new_campaign_review_belongs_to_the_department_of_its_campaign_and_has_no_title_yet() throws Exception {
        mockMvc.perform(request(STAFF, campaignDocument("campaign_review"))).andExpect(status().isAccepted())
                .andExpect(jsonPath("$.status").value("received"));

        assertThat(savedUpload().getDepartment()).isEqualTo("Beauty");
        assertThat(savedUpload().getTitle()).isNull();   // generated later from the campaign
        assertThat(event()).containsEntry("docType", "campaign_review").containsEntry("campaignId", 3);
    }

    @Test
    void a_new_data_table_takes_effect_at_once_and_uses_tableType_instead_of_docType() throws Exception {
        mockMvc.perform(csvRequest(STAFF, table("new"))).andExpect(status().isAccepted())
                .andExpect(jsonPath("$.status").value("received"));

        assertThat(event())
                .containsEntry("contentKind", "data_table")
                .containsEntry("tableType", "campaign_products")
                .containsEntry("docType", null);
    }

    @Test
    void the_category_is_kept_on_the_upload_and_in_the_event() throws Exception {
        mockMvc.perform(request(STAFF, sop(), "category", "Compliance")).andExpect(status().isAccepted());

        assertThat(savedUpload().getCategory()).isEqualTo("Compliance");
        assertThat(event()).containsEntry("category", "Compliance");
    }

    // ------------------------------------------------------------------ waits for approval

    @Test
    void a_new_sop_for_all_departments_waits_and_publishes_nothing() throws Exception {
        mockMvc.perform(request(STAFF, sop(), "scope", "all")).andExpect(status().isAccepted())
                .andExpect(jsonPath("$.status").value("pending_approval"));

        assertThat(savedUpload().getDepartment()).isEqualTo("ALL");
        assertFileCount(1);                          // kept for the reviewer
        verify(outboxRepository, never()).save(any());
    }

    @Test
    void replacing_a_document_waits_and_records_which_version_it_replaces() throws Exception {
        document(42L, "sop", "Inventory Check", "Beauty", null);

        mockMvc.perform(request(STAFF, replacing(sop(), 42L))).andExpect(status().isAccepted())
                .andExpect(jsonPath("$.status").value("pending_approval"));

        assertThat(savedUpload().getReplacesUploadId()).isEqualTo(CURRENT_VERSION);
        verify(outboxRepository, never()).save(any());
    }

    @Test
    void the_replacing_sop_may_keep_its_own_title() throws Exception {
        Document own = document(42L, "sop", "Inventory Check", "Beauty", null);
        when(documentRepository.findSop("Beauty", "Inventory Check")).thenReturn(Optional.of(own));

        mockMvc.perform(request(STAFF, replacing(sop(), 42L))).andExpect(status().isAccepted());
    }

    @Test
    void anyone_may_propose_a_new_version_of_a_document_for_all_departments() throws Exception {
        document(42L, "sop", "Inventory Check", "ALL", null);

        mockMvc.perform(request(STAFF, replacing(sop(), 42L), "scope", "all")).andExpect(status().isAccepted())
                .andExpect(jsonPath("$.status").value("pending_approval"));
    }

    @Test
    void replacing_a_campaign_plan_waits_too() throws Exception {
        document(42L, "campaign_plan", "Plan", "Beauty", BEAUTY_CAMPAIGN);

        mockMvc.perform(request(STAFF, replacing(campaignDocument("campaign_plan"), 42L))).andExpect(status().isAccepted())
                .andExpect(jsonPath("$.status").value("pending_approval"));
    }

    @Test
    void replacing_a_data_table_waits_and_records_the_upload_behind_the_current_rows() throws Exception {
        when(dataTableRepository.hasRows(TableType.CAMPAIGN_PRODUCTS, BEAUTY_CAMPAIGN)).thenReturn(true);
        Upload current = new Upload();
        current.setUploadId(CURRENT_VERSION);
        when(uploadRepository.findFirstByCampaignIdAndTableTypeAndStatusOrderByUpdatedAtDesc(
                BEAUTY_CAMPAIGN, "campaign_products", "done")).thenReturn(Optional.of(current));

        mockMvc.perform(csvRequest(STAFF, table("replace"))).andExpect(status().isAccepted())
                .andExpect(jsonPath("$.status").value("pending_approval"));

        assertThat(savedUpload().getReplacesUploadId()).isEqualTo(CURRENT_VERSION);
    }

    @Test
    void rows_that_came_without_an_upload_can_be_replaced_too() throws Exception {
        when(dataTableRepository.hasRows(TableType.CAMPAIGN_PRODUCTS, BEAUTY_CAMPAIGN)).thenReturn(true);

        mockMvc.perform(csvRequest(STAFF, table("replace"))).andExpect(status().isAccepted());

        assertThat(savedUpload().getReplacesUploadId()).isNull();
    }

    // ------------------------------------------------------------------ 403: not this uploader's to touch

    @ParameterizedTest
    @CsvSource({EXECUTIVE, ADMIN})
    void executive_and_admin_never_upload(String user) throws Exception {
        MvcResult result = mockMvc.perform(request(user, sop())).andExpect(status().isForbidden()).andReturn();

        assertThat(errorOf(result)).contains("can't upload");
        assertNothingHappened();
    }

    @Test
    void a_document_of_another_department_can_not_be_replaced() throws Exception {
        document(42L, "sop", "Inventory Check", "Fresh Grocery", null);

        mockMvc.perform(request(STAFF, replacing(sop(), 42L))).andExpect(status().isForbidden());

        assertNothingHappened();
    }

    @Test
    void a_campaign_of_another_department_can_not_be_uploaded_to() throws Exception {
        Map<String, String> params = campaignDocument("campaign_review");
        params.put("campaignId", String.valueOf(GROCERY_CAMPAIGN));

        mockMvc.perform(request(STAFF, params)).andExpect(status().isForbidden());

        assertNothingHappened();
    }

    // ------------------------------------------------------------------ 409: the mode doesn't fit what exists

    @Test
    void a_new_sop_whose_title_exists_is_a_conflict() throws Exception {
        when(documentRepository.findSop("Beauty", "Inventory Check"))
                .thenReturn(Optional.of(new Document(42L, "sop", "Inventory Check", "Beauty", null, CURRENT_VERSION, 1)));

        MvcResult result = mockMvc.perform(request(STAFF, sop())).andExpect(status().isConflict()).andReturn();

        assertThat(errorOf(result)).contains("mode=replace");
        assertNothingHappened();
    }

    @Test
    void the_same_title_in_another_scope_is_a_different_sop() throws Exception {
        when(documentRepository.findSop("ALL", "Inventory Check"))
                .thenReturn(Optional.of(new Document(42L, "sop", "Inventory Check", "ALL", null, CURRENT_VERSION, 1)));

        mockMvc.perform(request(STAFF, sop())).andExpect(status().isAccepted());
    }

    @Test
    void a_new_plan_for_a_campaign_that_has_one_is_a_conflict() throws Exception {
        when(documentRepository.findByCampaignIdAndDocType(BEAUTY_CAMPAIGN, "campaign_plan"))
                .thenReturn(Optional.of(new Document(42L, "campaign_plan", "Plan", "Beauty", BEAUTY_CAMPAIGN, CURRENT_VERSION, 1)));

        mockMvc.perform(request(STAFF, campaignDocument("campaign_plan"))).andExpect(status().isConflict());

        assertNothingHappened();
    }

    @Test
    void replacing_a_document_that_does_not_exist_is_a_conflict() throws Exception {
        MvcResult result = mockMvc.perform(request(STAFF, replacing(sop(), 42L))).andExpect(status().isConflict()).andReturn();

        assertThat(errorOf(result)).contains("nothing to replace");
        assertNothingHappened();
    }

    @Test
    void renaming_an_sop_to_a_title_another_sop_has_is_a_conflict() throws Exception {
        document(42L, "sop", "Old Title", "Beauty", null);
        when(documentRepository.findSop("Beauty", "Inventory Check"))
                .thenReturn(Optional.of(new Document(43L, "sop", "Inventory Check", "Beauty", null, UUID.randomUUID(), 1)));

        mockMvc.perform(request(STAFF, replacing(sop(), 42L))).andExpect(status().isConflict());

        assertNothingHappened();
    }

    @Test
    void a_new_data_table_for_a_campaign_with_rows_is_a_conflict() throws Exception {
        when(dataTableRepository.hasRows(TableType.CAMPAIGN_PRODUCTS, BEAUTY_CAMPAIGN)).thenReturn(true);

        MvcResult result = mockMvc.perform(csvRequest(STAFF, table("new"))).andExpect(status().isConflict()).andReturn();

        assertThat(errorOf(result)).contains("mode=replace");
        assertNothingHappened();
    }

    @Test
    void replacing_a_data_table_with_no_rows_is_a_conflict() throws Exception {
        MvcResult result = mockMvc.perform(csvRequest(STAFF, table("replace"))).andExpect(status().isConflict()).andReturn();

        assertThat(errorOf(result)).contains("nothing to replace");
        assertNothingHappened();
    }

    // ------------------------------------------------------------------ 400: malformed requests

    @Test
    void replacing_with_a_document_of_another_type_is_a_bad_request() throws Exception {
        document(42L, "campaign_plan", "Plan", "Beauty", BEAUTY_CAMPAIGN);

        mockMvc.perform(request(STAFF, replacing(campaignDocument("campaign_review"), 42L))).andExpect(status().isBadRequest());

        assertNothingHappened();
    }

    @Test
    void replacing_a_document_of_another_campaign_is_a_bad_request() throws Exception {
        document(42L, "campaign_plan", "Plan", "Beauty", 99L);

        mockMvc.perform(request(STAFF, replacing(campaignDocument("campaign_plan"), 42L))).andExpect(status().isBadRequest());

        assertNothingHappened();
    }

    record BadCase(String name, Map<String, String> params, String filename, String expectedFragment) {
        @Override
        public String toString() {
            return name;   // shown in the test report, so a failing case is obvious at a glance
        }

        BadCase withFragment(String fragment) {
            return new BadCase(name, params, filename, fragment);
        }
    }

    private static BadCase bad(String name, Map<String, String> params, String expectedFragment) {
        return new BadCase(name, params, "note.md", expectedFragment);
    }

    private static BadCase badFile(String name, Map<String, String> params, String filename) {
        return new BadCase(name, params, filename, "accepts");
    }

    private static final Map<String, String> SOP = Map.of("contentKind", "document", "docType", "sop", "mode", "new",
            "scope", "department", "title", "Inventory Check");

    private static Map<String, String> with(Map<String, String> base, String... pairs) {
        Map<String, String> params = new HashMap<>(base);
        for (int i = 0; i < pairs.length; i += 2) {
            params.put(pairs[i], pairs[i + 1]);
        }
        return params;
    }

    private static Map<String, String> without(Map<String, String> base, String key) {
        Map<String, String> params = new HashMap<>(base);
        params.remove(key);
        return params;
    }

    static Stream<BadCase> badRequests() {
        Map<String, String> review = Map.of("contentKind", "document", "docType", "campaign_review", "mode", "new", "campaignId", "3");
        Map<String, String> table = Map.of("contentKind", "data_table", "tableType", "campaign_products", "mode", "new", "campaignId", "3");
        return Stream.of(
                bad("no mode", without(SOP, "mode"), "mode must be"),
                bad("unknown mode", with(SOP, "mode", "update"), "update"),
                bad("document without docType", without(SOP, "docType"), "docType"),
                bad("misspelled docType", with(review, "docType", "campain_plan"), "campain_plan"),
                bad("invalid contentKind", with(SOP, "contentKind", "video"), "video"),
                bad("campaign_review without campaignId", without(review, "campaignId"), "requires a campaignId"),
                bad("sop with a campaignId", with(SOP, "campaignId", "3"), "must not have a campaignId"),
                bad("sop without a title", without(SOP, "title"), "requires a title"),
                bad("sop with a blank title", with(SOP, "title", "   "), "requires a title"),
                bad("sop without a scope", without(SOP, "scope"), "scope"),
                bad("sop with an unknown scope", with(SOP, "scope", "company"), "company"),
                bad("campaign_review with a title", with(review, "title", "My review"), "only for sop"),
                bad("campaign_review with a scope", with(review, "scope", "all"), "only for sop"),
                bad("unknown campaignId", with(review, "campaignId", "99999"), "unknown campaignId"),
                bad("replace without replacesDocId", with(SOP, "mode", "replace"), "requires replacesDocId"),
                bad("new with replacesDocId", with(SOP, "replacesDocId", "42"), "only for replacing"),
                badFile("data_table without tableType", without(table, "tableType"), "x.csv").withFragment("tableType"),
                badFile("misspelled tableType", with(table, "tableType", "campaign_product"), "x.csv").withFragment("campaign_product"),
                badFile("data_table without campaignId", without(table, "campaignId"), "x.csv").withFragment("requires a campaignId"),
                badFile("data_table with replacesDocId", with(table, "replacesDocId", "42"), "x.csv").withFragment("only for replacing"),
                badFile("an executable as a document", SOP, "tool.exe"),
                badFile("a csv as a document", SOP, "data.csv"),
                badFile("a document as a data table", table, "note.md"),
                badFile("an xlsx as a data table", table, "data.xlsx"),
                badFile("no extension", SOP, "README"));
    }

    @ParameterizedTest(name = "{0}")
    @MethodSource("badRequests")
    void malformed_requests_get_400_with_a_specific_reason_and_no_side_effects(BadCase bad) throws Exception {
        MvcResult result = mockMvc.perform(request(STAFF, bad.params(), bad.filename()))
                .andExpect(status().isBadRequest())
                .andReturn();

        assertThat(errorOf(result)).contains(bad.expectedFragment());
        assertNothingHappened();
    }

    @Test
    void the_file_extension_is_checked_regardless_of_case() throws Exception {
        mockMvc.perform(request(STAFF, sop(), "NOTE.MD")).andExpect(status().isAccepted());
    }

    /**
     * The file name is whatever the client sent. Joined onto the disk path as is, "../../evil.md" would write
     * outside the upload folder. A malicious name is flattened, the file lands in the upload folder, and the
     * published storageKey has no path separator, so the consumer can't escape either.
     */
    @Test
    void a_malicious_filename_can_not_escape_the_upload_directory() throws Exception {
        mockMvc.perform(request(STAFF, sop(), "../../evil.md")).andExpect(status().isAccepted());

        String storageKey = savedUpload().getStorageKey();
        assertThat(storageKey).doesNotContain("/").doesNotContain("\\");
        assertFileCount(1);
        assertThat(uploadDir.resolve(storageKey)).exists();
        assertThat(uploadDir.resolve(storageKey).normalize().getParent()).isEqualTo(uploadDir.normalize());
    }

    @ParameterizedTest
    @CsvSource(delimiter = '|', nullValues = "NULL", value = {
            "note.md                | note.md",
            "../../evil.md          | .._.._evil.md",
            "C:\\Windows\\sys.md     | C__Windows_sys.md",
            "a/b\\c.md              | a_b_c.md",
            "Café SOP.md            | Café SOP.md",
            "..                     | upload",
            "NULL                   | upload",
    })
    void sanitizeFilename_flattens_everything_that_could_be_a_path(String input, String expected) {
        assertThat(UploadStorage.sanitizeFilename(input)).isEqualTo(expected);
    }

    // ------------------------------------------------------------------ 401: who is asking

    @ParameterizedTest(name = "X-User-Id = {0}")
    @CsvSource(nullValues = "NULL", value = {"NULL", "99", "abc", "''"})
    void an_unknown_or_missing_user_gets_401_before_anything_else(String header) throws Exception {
        MockMultipartHttpServletRequestBuilder builder = multipart("/documents/upload")
                .file(new MockMultipartFile("file", "note.md", "text/markdown", "# Content".getBytes(UTF_8)));
        if (header != null) {
            builder.header("X-User-Id", header);
        }
        sop().forEach(builder::param);

        mockMvc.perform(builder).andExpect(status().isUnauthorized());

        assertNothingHappened();
    }

    // ------------------------------------------------------------------ recording fails

    /** The upload and its event are one transaction: if it fails, neither exists, so the stored file must go. */
    @Test
    void if_recording_fails_the_file_is_removed_and_the_upload_did_not_take_effect() throws Exception {
        when(uploadRepository.save(any())).thenThrow(new RuntimeException("database down"));

        MvcResult result = mockMvc.perform(request(STAFF, sop())).andExpect(status().isInternalServerError()).andReturn();

        assertThat(errorOf(result)).contains("did not take effect");
        assertFileCount(0);
    }

    // ------------------------------------------------------------------ the status endpoint

    @ParameterizedTest
    @CsvSource({STAFF, MANAGER, ADMIN})
    void the_status_is_visible_to_the_uploader_the_reviewer_and_admin(String viewer) throws Exception {
        Upload upload = uploadBy(5L);
        upload.changeStatus("failed", "row 3, units_sold: 'abc' is not a whole number");

        mockMvc.perform(get("/documents/{id}/status", upload.getUploadId()).header("X-User-Id", viewer))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.uploadId").value(upload.getUploadId().toString()))
                .andExpect(jsonPath("$.status").value("failed"))
                .andExpect(jsonPath("$.error").value("row 3, units_sold: 'abc' is not a whole number"));
    }

    @ParameterizedTest
    @CsvSource({OTHER_STAFF, GROCERY_STAFF, GROCERY_MANAGER, EXECUTIVE})
    void the_status_is_hidden_from_everyone_else(String viewer) throws Exception {
        Upload upload = uploadBy(5L);

        mockMvc.perform(get("/documents/{id}/status", upload.getUploadId()).header("X-User-Id", viewer))
                .andExpect(status().isForbidden());
    }

    @Test
    void an_automatic_sync_has_no_uploader_so_only_admin_sees_it() throws Exception {
        Upload upload = uploadBy(null);
        upload.setSource("auto");

        mockMvc.perform(get("/documents/{id}/status", upload.getUploadId()).header("X-User-Id", ADMIN)).andExpect(status().isOk());
        mockMvc.perform(get("/documents/{id}/status", upload.getUploadId()).header("X-User-Id", STAFF)).andExpect(status().isForbidden());
    }

    @Test
    void the_status_of_an_unknown_upload_is_404() throws Exception {
        mockMvc.perform(get("/documents/{id}/status", UUID.randomUUID()).header("X-User-Id", STAFF)).andExpect(status().isNotFound());
    }

    @Test
    void the_status_needs_a_known_user() throws Exception {
        mockMvc.perform(get("/documents/{id}/status", UUID.randomUUID())).andExpect(status().isUnauthorized());
    }

    // ------------------------------------------------------------------ helpers

    private Upload uploadBy(Long uploaderId) {
        Upload upload = new Upload();
        upload.setUploadId(UUID.randomUUID());
        upload.setUploadedBy(uploaderId);
        upload.setSource("manual");
        upload.changeStatus("received", null);
        when(uploadRepository.findById(eq(upload.getUploadId()))).thenReturn(Optional.of(upload));
        return upload;
    }

    private Document document(Long id, String type, String title, String department, Long campaignId) {
        Document document = new Document(id, type, title, department, campaignId, CURRENT_VERSION, 1);
        when(documentRepository.findById(id)).thenReturn(Optional.of(document));
        return document;
    }

    private Upload savedUpload() {
        ArgumentCaptor<Upload> upload = ArgumentCaptor.forClass(Upload.class);
        verify(uploadRepository).save(upload.capture());
        return upload.getValue();
    }

    private void assertNothingHappened() throws Exception {
        verify(uploadRepository, never()).save(any());
        verifyNoInteractions(outboxRepository);
        assertFileCount(0);   // no file left on disk either
    }

    private static Map<String, String> sop() {
        return new HashMap<>(SOP);
    }

    private static Map<String, String> campaignDocument(String docType) {
        return new HashMap<>(Map.of("contentKind", "document", "docType", docType, "mode", "new", "campaignId", "3"));
    }

    private static Map<String, String> table(String mode) {
        return new HashMap<>(Map.of("contentKind", "data_table", "tableType", "campaign_products", "mode", mode, "campaignId", "3"));
    }

    private static Map<String, String> replacing(Map<String, String> params, long docId) {
        params.put("mode", "replace");
        params.put("replacesDocId", String.valueOf(docId));
        return params;
    }

    private static MockMultipartHttpServletRequestBuilder request(String user, Map<String, String> params, String filename) {
        MockMultipartHttpServletRequestBuilder builder = multipart("/documents/upload")
                .file(new MockMultipartFile("file", filename, "application/octet-stream", "content".getBytes(UTF_8)));
        builder.header("X-User-Id", user);
        params.forEach(builder::param);
        return builder;
    }

    private static MockMultipartHttpServletRequestBuilder request(String user, Map<String, String> params) {
        return request(user, params, "note.md");
    }

    private static MockMultipartHttpServletRequestBuilder request(String user, Map<String, String> params, String name, String value) {
        Map<String, String> all = new HashMap<>(params);
        all.put(name, value);
        return request(user, all);
    }

    private static MockMultipartHttpServletRequestBuilder csvRequest(String user, Map<String, String> params) {
        return request(user, params, "data.csv");
    }
}
