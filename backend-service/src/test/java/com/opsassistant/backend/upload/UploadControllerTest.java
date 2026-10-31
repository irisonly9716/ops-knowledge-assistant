package com.opsassistant.backend.upload;

import static java.nio.charset.StandardCharsets.UTF_8;
import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.multipart;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.opsassistant.backend.campaign.CampaignRepository;
import com.opsassistant.backend.department.DepartmentRepository;
import com.opsassistant.backend.user.User;
import com.opsassistant.backend.user.UserRepository;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;
import java.util.Optional;
import java.util.stream.Stream;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.junit.jupiter.params.provider.MethodSource;
import org.mockito.ArgumentCaptor;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import tools.jackson.databind.json.JsonMapper;

/**
 * Endpoint tests for UploadController. @WebMvcTest loads only the web layer (controller, request
 * parsing, exception handling, JSON serialization), not the whole application, so Postgres and Kafka
 * don't need to run. The repositories and UploadService are replaced with fakes via @MockitoBean.
 *
 * The controller doesn't talk to Kafka any more: it hands the upload and its event to UploadService,
 * which writes both in one transaction (the outbox). Publishing and its failure modes are tested in
 * OutboxRelayTest; the transaction itself in OutboxIntegrationTest.
 *
 * Groups: success path (are the uploads row and the event right) / identity and parameter validation
 * (everything that should be rejected is, with no side effects) / recording fails.
 */
@WebMvcTest(UploadController.class)
class UploadControllerTest {

    /** Uploaded files go to this temporary folder, never the real backend-service/uploads. */
    @TempDir
    static Path uploadDir;

    @DynamicPropertySource
    static void properties(DynamicPropertyRegistry registry) {
        registry.add("document.upload-dir", () -> uploadDir.toString());
    }

    @Autowired
    MockMvc mockMvc;

    @MockitoBean
    DepartmentRepository departmentRepository;
    @MockitoBean
    CampaignRepository campaignRepository;
    @MockitoBean
    UserRepository userRepository;
    @MockitoBean
    UploadService uploadService;

    private final JsonMapper json = JsonMapper.builder().build();

    @BeforeEach
    void setUp() throws IOException {
        try (Stream<Path> files = Files.list(uploadDir)) {
            files.forEach(p -> p.toFile().delete()); // every test starts with an empty folder
        }
        // only this department and this campaign exist in the "database"; everything else gets Mockito's default false
        when(departmentRepository.existsByName("Beauty")).thenReturn(true);
        when(campaignRepository.existsById(3L)).thenReturn(true);
        // user 5 is a Beauty staff member; any other id is unknown
        when(userRepository.findById(5L)).thenReturn(Optional.of(new User(5L, "Test Staff", 1L, "staff")));
    }

    // ------------------------------------------------------------------ success path

    @Test
    void sop_upload_is_accepted_and_recorded_with_its_event() throws Exception {
        MvcResult result = mockMvc.perform(request(sopParams()))
                .andExpect(status().isAccepted())
                .andReturn();
        Map<String, Object> response = readMap(body(result));
        String uploadId = (String) response.get("uploadId");
        assertThat(response).containsEntry("status", "accepted");

        Recorded recorded = recorded();
        UploadEvent event = recorded.event();
        assertThat(event.uploadId()).isEqualTo(uploadId);
        assertThat(event.storageKey()).isEqualTo(uploadId + "-note.md"); // a logical key (the file name), not a physical path
        assertThat(event.contentKind()).isEqualTo(ContentKind.DOCUMENT);
        assertThat(event.docType()).isEqualTo(DocType.SOP);
        assertThat(event.department()).isEqualTo("ALL");
        assertThat(event.campaignId()).isNull();
        assertThat(event.source()).isEqualTo(Source.MANUAL);
        assertThat(uploadDir.resolve(uploadId + "-note.md")).exists(); // the file really was saved

        // the uploads row: who uploaded it, the SOP title, status received
        Upload upload = recorded.upload();
        assertThat(upload.getUploadId()).hasToString(uploadId);
        assertThat(upload.getStatus()).isEqualTo("received");
        assertThat(upload.getUploadedBy()).isEqualTo(5L);
        assertThat(upload.getTitle()).isEqualTo("Account Restriction Handling");
        assertThat(upload.getStorageKey()).isEqualTo(uploadId + "-note.md");
        assertThat(upload.getOriginalFilename()).isEqualTo("note.md");
        assertThat(upload.getDocType()).isEqualTo("sop");
    }

    @Test
    void campaign_review_upload_carries_the_campaign_id() throws Exception {
        mockMvc.perform(request(Map.of("contentKind", "document", "docType", "campaign_review",
                        "department", "Beauty", "campaignId", "3")))
                .andExpect(status().isAccepted());

        Recorded recorded = recorded();
        assertThat(recorded.event().docType()).isEqualTo(DocType.CAMPAIGN_REVIEW);
        assertThat(recorded.event().campaignId()).isEqualTo(3L);
        assertThat(recorded.upload().getCampaignId()).isEqualTo(3L);
        assertThat(recorded.upload().getTitle()).isNull(); // generated later from the campaign
    }

    @Test
    void data_table_upload_uses_tableType_instead_of_docType() throws Exception {
        mockMvc.perform(request(Map.of("contentKind", "data_table", "tableType", "campaign_products",
                        "department", "Beauty", "campaignId", "3")))
                .andExpect(status().isAccepted());

        Recorded recorded = recorded();
        assertThat(recorded.event().contentKind()).isEqualTo(ContentKind.DATA_TABLE);
        assertThat(recorded.event().tableType()).isEqualTo(TableType.CAMPAIGN_PRODUCTS);
        assertThat(recorded.event().docType()).isNull();
        assertThat(recorded.upload().getTableType()).isEqualTo("campaign_products");
    }

    /**
     * The file name is whatever the client sent. Joined onto the disk path as is, "../../evil.md"
     * would write outside the upload folder. This checks that a malicious name is flattened into a
     * safe single-level name, the file really lands in the upload folder, and the published
     * storageKey has no path separator (so the consumer can't escape either when it joins the path).
     */
    @Test
    void a_malicious_filename_cannot_escape_the_upload_directory() throws Exception {
        MockMultipartFile evil = new MockMultipartFile(
                "file", "../../evil.md", "text/markdown", "x".getBytes(UTF_8));

        mockMvc.perform(multipart("/documents/upload").file(evil)
                        .header("X-User-Id", "5").param("title", "Evil")
                        .param("contentKind", "document").param("docType", "sop").param("department", "ALL"))
                .andExpect(status().isAccepted());

        String storageKey = recorded().event().storageKey();
        assertThat(storageKey).doesNotContain("/").doesNotContain("\\");
        assertThat(fileCount()).isEqualTo(1);
        assertThat(uploadDir.resolve(storageKey)).exists(); // right inside the upload folder
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
        assertThat(UploadController.sanitizeFilename(input)).isEqualTo(expected);
    }

    // ------------------------------------------------------------------ identity and parameter validation

    record BadCase(String name, Map<String, String> params, String expectedFragment) {
        @Override
        public String toString() {
            return name; // shown in the test report, so a failing case is obvious at a glance
        }
    }

    static Stream<BadCase> badRequests() {
        return Stream.of(
                new BadCase("document without docType",
                        Map.of("contentKind", "document", "department", "Beauty"), "docType"),
                new BadCase("misspelled docType (campain_plan)",
                        Map.of("contentKind", "document", "docType", "campain_plan",
                                "department", "Beauty", "campaignId", "3"), "campain_plan"),
                new BadCase("invalid contentKind (video)",
                        Map.of("contentKind", "video", "department", "Beauty"), "video"),
                new BadCase("campaign_review without campaignId",
                        Map.of("contentKind", "document", "docType", "campaign_review",
                                "department", "Beauty"), "requires a campaignId"),
                new BadCase("sop with a campaignId",
                        Map.of("contentKind", "document", "docType", "sop",
                                "department", "Beauty", "campaignId", "3"), "must not have a campaignId"),
                new BadCase("data_table without tableType",
                        Map.of("contentKind", "data_table", "department", "Beauty",
                                "campaignId", "3"), "tableType"),
                new BadCase("misspelled tableType (missing an s)",
                        Map.of("contentKind", "data_table", "tableType", "campaign_product",
                                "department", "Beauty", "campaignId", "3"), "campaign_product"),
                new BadCase("data_table without campaignId",
                        Map.of("contentKind", "data_table", "tableType", "campaign_products",
                                "department", "Beauty"), "requires a campaignId"),
                new BadCase("unknown department (Beuaty)",
                        Map.of("contentKind", "document", "docType", "sop", "department", "Beuaty",
                                "title", "Inventory Check"), "unknown department"),
                new BadCase("sop without a title",
                        Map.of("contentKind", "document", "docType", "sop", "department", "Beauty"),
                        "requires a title"),
                new BadCase("sop with a blank title",
                        Map.of("contentKind", "document", "docType", "sop", "department", "Beauty",
                                "title", "   "), "requires a title"),
                new BadCase("campaign_review with a title",
                        Map.of("contentKind", "document", "docType", "campaign_review", "department", "Beauty",
                                "campaignId", "3", "title", "My review"), "title is only for sop"),
                new BadCase("unknown campaignId (99999)",
                        Map.of("contentKind", "document", "docType", "campaign_review",
                                "department", "Beauty", "campaignId", "99999"), "unknown campaignId"));
    }

    @ParameterizedTest(name = "{0}")
    @MethodSource("badRequests")
    void invalid_requests_get_400_with_a_specific_reason_and_no_side_effects(BadCase bad) throws Exception {
        MvcResult result = mockMvc.perform(request(bad.params()))
                .andExpect(status().isBadRequest())
                .andReturn();

        assertThat((String) readMap(body(result)).get("error")).contains(bad.expectedFragment());
        verifyNoInteractions(uploadService); // a rejected request must not record anything
        assertThat(fileCount()).isZero();    // nor leave any file on disk
    }

    @ParameterizedTest(name = "X-User-Id = {0}")
    @CsvSource(nullValues = "NULL", value = {"NULL", "99", "abc"})
    void unknown_or_missing_user_gets_401_before_anything_else(String header) throws Exception {
        var builder = multipart("/documents/upload")
                .file(new MockMultipartFile("file", "note.md", "text/markdown", "# Content".getBytes(UTF_8)));
        if (header != null) {
            builder.header("X-User-Id", header);
        }
        sopParams().forEach(builder::param);

        mockMvc.perform(builder).andExpect(status().isUnauthorized());

        verifyNoInteractions(uploadService);
        assertThat(fileCount()).isZero();
    }

    // ------------------------------------------------------------------ recording fails

    /**
     * The upload and its event are one transaction: if recording fails, neither exists, so the stored
     * file belongs to nobody and must go.
     */
    @Test
    void if_recording_fails_the_file_is_removed_and_the_upload_did_not_take_effect() throws Exception {
        doThrow(new RuntimeException("database down")).when(uploadService).record(any(), any());

        MvcResult result = mockMvc.perform(request(sopParams()))
                .andExpect(status().isInternalServerError())
                .andReturn();

        assertThat((String) readMap(body(result)).get("error")).contains("did not take effect");
        assertThat(fileCount()).isZero();
    }

    // ------------------------------------------------------------------ helpers

    record Recorded(Upload upload, UploadEvent event) {
    }

    /** What the controller handed to UploadService (exactly one call). */
    private Recorded recorded() {
        ArgumentCaptor<Upload> upload = ArgumentCaptor.forClass(Upload.class);
        ArgumentCaptor<UploadEvent> event = ArgumentCaptor.forClass(UploadEvent.class);
        verify(uploadService).record(upload.capture(), event.capture());
        return new Recorded(upload.getValue(), event.getValue());
    }

    private static Map<String, String> sopParams() {
        return Map.of("contentKind", "document", "docType", "sop", "department", "ALL",
                "title", "Account Restriction Handling");
    }

    private static org.springframework.test.web.servlet.request.MockMultipartHttpServletRequestBuilder request(
            Map<String, String> params) {
        var builder = multipart("/documents/upload")
                .file(new MockMultipartFile("file", "note.md", "text/markdown", "# Content".getBytes(UTF_8)))
                .header("X-User-Id", "5");
        params.forEach(builder::param);
        return builder;
    }

    /** MockHttpServletResponse decodes as ISO-8859-1 by default; non-ASCII error messages need UTF-8 explicitly. */
    private static String body(MvcResult result) throws Exception {
        return result.getResponse().getContentAsString(UTF_8);
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> readMap(String text) {
        return json.readValue(text, Map.class);
    }

    private long fileCount() throws IOException {
        try (Stream<Path> files = Files.list(uploadDir)) {
            return files.count();
        }
    }
}
