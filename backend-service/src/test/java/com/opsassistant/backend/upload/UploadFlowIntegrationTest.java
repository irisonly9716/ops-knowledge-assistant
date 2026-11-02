package com.opsassistant.backend.upload;

import static java.nio.charset.StandardCharsets.UTF_8;
import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.multipart;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Assumptions;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.ResultActions;
import org.springframework.test.web.servlet.request.MockMultipartHttpServletRequestBuilder;
import tools.jackson.databind.json.JsonMapper;

/**
 * Whole requests against the real Postgres (docker compose): the queries and transactions the endpoint tests
 * fake. The native natural-key query, the approval queues, what a decision commits, the campaign search.
 *
 * Runs against the development database and its mock data: only touches rows it creates, deletes them
 * afterwards. Kafka is a Mockito fake and the relay is off, so nothing is published.
 * Run with: .\mvnw.cmd test -DexcludedGroups=none -Dgroups=integration
 */
@Tag("integration")
@SpringBootTest(properties = "outbox.relay.enabled=false")
@AutoConfigureMockMvc
class UploadFlowIntegrationTest {

    @TempDir
    static Path uploadDir;

    @DynamicPropertySource
    static void properties(DynamicPropertyRegistry registry) {
        registry.add("document.upload-dir", () -> uploadDir.toString());
    }

    @Autowired
    MockMvc mockMvc;
    @Autowired
    JdbcTemplate jdbc;
    @MockitoBean
    KafkaTemplate<String, String> kafkaTemplate;

    private final JsonMapper json = JsonMapper.builder().build();
    private final String filePrefix = "flow-it-" + UUID.randomUUID().toString().substring(0, 8);
    private final String title = "Flow Test " + filePrefix;

    private String staff;
    private String otherStaff;
    private String manager;
    private String otherManager;
    private String executive;
    private String admin;
    private long campaignWithRows;
    private long campaignWithoutRows;

    @BeforeEach
    void setUp() {
        List<Long> beautyStaff = jdbc.queryForList("SELECT user_id FROM users WHERE role = 'staff' AND department_id = 1 ORDER BY user_id", Long.class);
        Assumptions.assumeTrue(beautyStaff.size() >= 2, "needs the mock data");
        staff = String.valueOf(beautyStaff.get(0));
        otherStaff = String.valueOf(beautyStaff.get(1));
        manager = userWith("role = 'manager' AND department_id = 1");
        otherManager = userWith("role = 'manager' AND department_id = 2");
        executive = userWith("role = 'executive'");
        admin = userWith("role = 'admin'");
        campaignWithRows = jdbc.queryForObject("""
                SELECT min(campaign_id) FROM campaign_products JOIN campaigns USING (campaign_id) WHERE department_id = 1""", Long.class);
        List<Long> empty = jdbc.queryForList("""
                SELECT campaign_id FROM campaigns c WHERE department_id = 1
                AND NOT EXISTS (SELECT 1 FROM campaign_products p WHERE p.campaign_id = c.campaign_id) ORDER BY campaign_id""", Long.class);
        Assumptions.assumeFalse(empty.isEmpty(), "needs a campaign with no product rows (a running one in the mock data)");
        campaignWithoutRows = empty.get(0);
    }

    private String userWith(String condition) {
        return String.valueOf(jdbc.queryForObject("SELECT min(user_id) FROM users WHERE " + condition, Long.class));
    }

    @AfterEach
    void cleanUp() {
        jdbc.update("DELETE FROM outbox WHERE message_key IN (SELECT upload_id::text FROM uploads WHERE original_filename LIKE ?)", filePrefix + "%");
        jdbc.update("DELETE FROM documents WHERE upload_id IN (SELECT upload_id FROM uploads WHERE original_filename LIKE ?)", filePrefix + "%");
        jdbc.update("DELETE FROM uploads WHERE original_filename LIKE ?", filePrefix + "%");
    }

    // ------------------------------------------------------------------ recording

    @Test
    void a_new_sop_is_recorded_with_its_event_in_the_outbox() throws Exception {
        String id = uploadId(sopUpload(staff, "new").param("scope", "department"));

        assertThat(statusOf(id)).isEqualTo("received");
        assertThat(eventsOf(id)).isEqualTo(1);
    }

    @Test
    void a_new_sop_for_all_is_recorded_with_no_event_until_it_is_approved() throws Exception {
        String id = uploadId(sopUpload(staff, "new").param("scope", "all"));

        assertThat(statusOf(id)).isEqualTo("pending_approval");
        assertThat(eventsOf(id)).isZero();
    }

    /** The native query uses the expression of the unique index: this is what the index would refuse later. */
    @Test
    void the_natural_key_of_an_sop_ignores_case_outer_spaces_and_unicode_forms() throws Exception {
        existingDocument("Beauty", title + " ﬁnal");   // the ligature "ﬁ" is what NFKC turns into "fi"

        mockMvc.perform(upload(staff, "document", "new").param("docType", "sop").param("scope", "department")
                        .param("title", "  " + title.toUpperCase() + " FINAL  "))
                .andExpect(status().isConflict());
    }

    @Test
    void the_same_title_in_another_department_is_a_different_sop() throws Exception {
        existingDocument("Fresh Grocery", title);

        String id = uploadId(sopUpload(staff, "new").param("scope", "department"));

        assertThat(statusOf(id)).isEqualTo("received");
    }

    @Test
    void a_data_table_for_a_campaign_without_rows_is_new_and_one_with_rows_must_be_a_replacement() throws Exception {
        uploadId(table(staff, "new", campaignWithoutRows));

        mockMvc.perform(table(staff, "new", campaignWithRows)).andExpect(status().isConflict());
        mockMvc.perform(table(staff, "replace", campaignWithoutRows)).andExpect(status().isConflict());
        String replacement = uploadId(table(staff, "replace", campaignWithRows));
        assertThat(statusOf(replacement)).isEqualTo("pending_approval");
    }

    // ------------------------------------------------------------------ the approval flow

    @Test
    void a_staff_replacement_waits_in_their_managers_queue_and_approving_it_writes_the_event() throws Exception {
        UUID current = existingDocument("Beauty", title);
        long docId = jdbc.queryForObject("SELECT doc_id FROM documents WHERE upload_id = ?", Long.class, current);
        String id = uploadId(sopUpload(staff, "replace").param("scope", "department").param("replacesDocId", String.valueOf(docId)));

        assertThat(statusOf(id)).isEqualTo("pending_approval");
        assertThat(replacesOf(id)).isEqualTo(current);
        assertThat(queueOf(manager)).contains(id);
        assertThat(queueOf(otherManager)).doesNotContain(id);
        assertThat(queueOf(executive)).doesNotContain(id);
        mockMvc.perform(decide(id, "approve", otherManager)).andExpect(status().isForbidden());
        mockMvc.perform(decide(id, "approve", admin)).andExpect(status().isForbidden());
        assertThat(statusOf(id)).isEqualTo("pending_approval");

        mockMvc.perform(decide(id, "approve", manager).param("comment", "fine")).andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("received"));

        assertThat(statusOf(id)).isEqualTo("received");
        assertThat(eventsOf(id)).isEqualTo(1);
        assertThat(queueOf(manager)).doesNotContain(id);
        assertThat(String.valueOf(jdbc.queryForObject("SELECT reviewed_by FROM uploads WHERE upload_id = ?::uuid", Long.class, id))).isEqualTo(manager);
        assertThat(jdbc.queryForObject("SELECT reviewed_at IS NOT NULL FROM uploads WHERE upload_id = ?::uuid", Boolean.class, id)).isTrue();
        assertThat(jdbc.queryForObject("SELECT review_comment FROM uploads WHERE upload_id = ?::uuid", String.class, id)).isEqualTo("fine");
    }

    @Test
    void a_managers_upload_goes_to_the_executive() throws Exception {
        String id = uploadId(sopUpload(manager, "new").param("scope", "all"));

        assertThat(queueOf(executive)).contains(id);
        assertThat(queueOf(manager)).doesNotContain(id);
        mockMvc.perform(decide(id, "approve", manager)).andExpect(status().isForbidden());

        mockMvc.perform(decide(id, "approve", executive)).andExpect(status().isOk());
        assertThat(eventsOf(id)).isEqualTo(1);
    }

    /** The void decision must survive: the transaction commits, and the response is still a conflict. */
    @Test
    void approving_a_stale_request_commits_the_rejection_and_removes_the_file() throws Exception {
        UUID current = existingDocument("Beauty", title);
        long docId = jdbc.queryForObject("SELECT doc_id FROM documents WHERE upload_id = ?", Long.class, current);
        String id = uploadId(sopUpload(staff, "replace").param("scope", "department").param("replacesDocId", String.valueOf(docId)));
        String storageKey = jdbc.queryForObject("SELECT storage_key FROM uploads WHERE upload_id = ?::uuid", String.class, id);

        // Someone else's new version went live while this one waited
        UUID newer = insertUpload();
        jdbc.update("UPDATE documents SET upload_id = ? WHERE doc_id = ?", newer, docId);

        mockMvc.perform(decide(id, "approve", manager)).andExpect(status().isConflict())
                .andExpect(jsonPath("$.error").value(org.hamcrest.Matchers.containsString("superseded")));

        assertThat(statusOf(id)).isEqualTo("rejected");
        assertThat(eventsOf(id)).isZero();
        assertThat(uploadDir.resolve(storageKey)).doesNotExist();
    }

    @Test
    void rejecting_commits_the_decision_and_removes_the_file() throws Exception {
        String id = uploadId(sopUpload(staff, "new").param("scope", "all"));
        String storageKey = jdbc.queryForObject("SELECT storage_key FROM uploads WHERE upload_id = ?::uuid", String.class, id);
        assertThat(uploadDir.resolve(storageKey)).exists();

        mockMvc.perform(decide(id, "reject", manager).param("comment", "no")).andExpect(status().isOk());

        assertThat(statusOf(id)).isEqualTo("rejected");
        assertThat(eventsOf(id)).isZero();
        assertThat(uploadDir.resolve(storageKey)).doesNotExist();
        mockMvc.perform(decide(id, "approve", manager)).andExpect(status().isConflict());   // decided once
    }

    @Test
    void a_data_table_replacement_compares_with_the_last_finished_upload() throws Exception {
        String first = uploadId(table(staff, "new", campaignWithoutRows));
        jdbc.update("UPDATE uploads SET status = 'done', updated_at = now() WHERE upload_id = ?::uuid", first);
        jdbc.update("INSERT INTO campaign_products (campaign_id, product_name) VALUES (?, ?)", campaignWithoutRows, filePrefix);
        try {
            String replacement = uploadId(table(staff, "replace", campaignWithoutRows));
            assertThat(replacesOf(replacement)).hasToString(first);

            // A later upload finishes first: the waiting one is now out of date
            jdbc.update("UPDATE uploads SET status = 'done', updated_at = now() WHERE upload_id = ?::uuid", uploadId(table(staff, "replace", campaignWithoutRows)));

            mockMvc.perform(decide(replacement, "approve", manager)).andExpect(status().isConflict());
            assertThat(statusOf(replacement)).isEqualTo("rejected");
        } finally {
            jdbc.update("DELETE FROM campaign_products WHERE product_name = ?", filePrefix);
        }
    }

    // ------------------------------------------------------------------ reading

    @Test
    void the_status_endpoint_shows_an_upload_to_those_allowed() throws Exception {
        String id = uploadId(sopUpload(staff, "new").param("scope", "department"));

        for (String viewer : List.of(staff, manager, admin)) {
            mockMvc.perform(get("/documents/{id}/status", id).header("X-User-Id", viewer)).andExpect(status().isOk())
                    .andExpect(jsonPath("$.status").value("received"))
                    .andExpect(jsonPath("$.department").value("Beauty"));
        }
        for (String viewer : List.of(otherStaff, otherManager, executive)) {
            mockMvc.perform(get("/documents/{id}/status", id).header("X-User-Id", viewer)).andExpect(status().isForbidden());
        }
    }

    @Test
    void campaigns_by_month_are_limited_to_the_own_department_and_include_ones_that_span_it() throws Exception {
        Map<String, Object> campaign = jdbc.queryForMap("""
                SELECT campaign_id, to_char(start_date, 'YYYY-MM') AS first_month, to_char(end_date, 'YYYY-MM') AS last_month
                FROM campaigns WHERE department_id = 1 AND to_char(start_date, 'YYYY-MM') <> to_char(end_date, 'YYYY-MM')
                ORDER BY campaign_id LIMIT 1""");
        long campaignId = (Long) campaign.get("campaign_id");
        for (Object month : List.of(campaign.get("first_month"), campaign.get("last_month"))) {
            assertThat(campaignsIn(month.toString(), staff)).contains(campaignId);
            assertThat(campaignsIn(month.toString(), otherManager)).doesNotContain(campaignId);
            assertThat(campaignsIn(month.toString(), executive)).contains(campaignId);
        }
        assertThat(campaignsIn("1999-01", staff)).isEmpty();
    }

    // ------------------------------------------------------------------ helpers

    @SuppressWarnings("unchecked")
    private List<Long> campaignsIn(String month, String user) throws Exception {
        String body = mockMvc.perform(get("/campaigns").param("month", month).header("X-User-Id", user))
                .andExpect(status().isOk()).andReturn().getResponse().getContentAsString(UTF_8);
        return ((List<Map<String, Object>>) json.readValue(body, List.class)).stream()
                .map(c -> ((Number) c.get("campaignId")).longValue()).toList();
    }

    @SuppressWarnings("unchecked")
    private List<String> queueOf(String reviewer) throws Exception {
        String body = mockMvc.perform(get("/documents/approvals").header("X-User-Id", reviewer))
                .andExpect(status().isOk()).andReturn().getResponse().getContentAsString(UTF_8);
        return ((List<Map<String, Object>>) json.readValue(body, List.class)).stream()
                .map(u -> (String) u.get("uploadId")).toList();
    }

    private org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder decide(String id, String decision, String user) {
        return post("/documents/approvals/{id}/{decision}", id, decision).header("X-User-Id", user);
    }

    private String statusOf(String id) {
        return jdbc.queryForObject("SELECT status FROM uploads WHERE upload_id = ?::uuid", String.class, id);
    }

    private UUID replacesOf(String id) {
        return jdbc.queryForObject("SELECT replaces_upload_id FROM uploads WHERE upload_id = ?::uuid", UUID.class, id);
    }

    private Integer eventsOf(String id) {
        return jdbc.queryForObject("SELECT count(*) FROM outbox WHERE message_key = ?", Integer.class, id);
    }

    private String uploadId(MockMultipartHttpServletRequestBuilder request) throws Exception {
        ResultActions result = mockMvc.perform(request).andExpect(status().isAccepted());
        Map<?, ?> body = json.readValue(result.andReturn().getResponse().getContentAsString(UTF_8), Map.class);
        return (String) body.get("uploadId");
    }

    private MockMultipartHttpServletRequestBuilder sopUpload(String user, String mode) {
        return upload(user, "document", mode).param("docType", "sop").param("title", title);
    }

    private MockMultipartHttpServletRequestBuilder table(String user, String mode, long campaignId) {
        MockMultipartFile file = new MockMultipartFile("file", filePrefix + ".csv", "text/csv", "x".getBytes(UTF_8));
        MockMultipartHttpServletRequestBuilder request = multipart("/documents/upload").file(file);
        request.header("X-User-Id", user);
        request.param("contentKind", "data_table").param("tableType", "campaign_products").param("mode", mode)
                .param("campaignId", String.valueOf(campaignId));
        return request;
    }

    private MockMultipartHttpServletRequestBuilder upload(String user, String contentKind, String mode) {
        MockMultipartFile file = new MockMultipartFile("file", filePrefix + ".md", "text/markdown", "# Flow".getBytes(UTF_8));
        MockMultipartHttpServletRequestBuilder request = multipart("/documents/upload").file(file);
        request.header("X-User-Id", user);
        request.param("contentKind", contentKind).param("mode", mode);
        return request;
    }

    private UUID existingDocument(String department, String documentTitle) {
        UUID uploadId = insertUpload();
        jdbc.update("INSERT INTO documents (doc_type, title, department, upload_id) VALUES ('sop', ?, ?, ?)",
                documentTitle, department, uploadId);
        return uploadId;
    }

    private UUID insertUpload() {
        UUID uploadId = UUID.randomUUID();
        jdbc.update("""
                INSERT INTO uploads (upload_id, source, uploaded_by, content_kind, doc_type, department, title,
                                     original_filename, storage_key, status)
                VALUES (?, 'manual', ?, 'document', 'sop', 'Beauty', ?, ?, ?, 'done')""",
                uploadId, Long.valueOf(staff), title, filePrefix + ".md", uploadId.toString());
        return uploadId;
    }
}
