package com.opsassistant.backend.upload;

import static java.nio.charset.StandardCharsets.UTF_8;
import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
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
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;
import java.util.stream.Stream;
import org.apache.kafka.clients.producer.RecordMetadata;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.junit.jupiter.params.provider.MethodSource;
import org.mockito.ArgumentCaptor;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.support.SendResult;
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
 * don't need to run. The three external dependencies are replaced with fakes via @MockitoBean, which
 * is what lets the tests MAKE KAFKA FAIL ON DEMAND -- impossible with a real Kafka (you can't reliably
 * make it throw a particular exception at a particular moment).
 *
 * Groups: success path (is the message right, is the uploads row written) / identity and parameter
 * validation (everything that should be rejected is, with no side effects) / Kafka failure paths
 * (where testing found three real bugs; each has a regression test) and what they do to the uploads row.
 */
@WebMvcTest(UploadController.class)
class UploadControllerTest {

    /** Uploaded files go to this temporary folder, never the real backend-service/uploads. */
    @TempDir
    static Path uploadDir;

    @DynamicPropertySource
    static void properties(DynamicPropertyRegistry registry) {
        registry.add("document.upload-dir", () -> uploadDir.toString());
        registry.add("document.upload-topic", () -> "test-topic");
        // 15 seconds in production; the "outcome unknown" test really waits for one timeout, so 300 ms
        registry.add("document.send-timeout-ms", () -> "300");
    }

    @Autowired
    MockMvc mockMvc;

    @MockitoBean
    KafkaTemplate<String, String> kafkaTemplate;
    @MockitoBean
    DepartmentRepository departmentRepository;
    @MockitoBean
    CampaignRepository campaignRepository;
    @MockitoBean
    UserRepository userRepository;
    @MockitoBean
    UploadRepository uploadRepository;

    /** Every uploads row the controller saved, in order (a row saved twice appears twice). */
    private final List<String> savedStatuses = new java.util.ArrayList<>();

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
        savedStatuses.clear();
        when(uploadRepository.save(any(Upload.class))).thenAnswer(invocation -> {
            Upload upload = invocation.getArgument(0);
            savedStatuses.add(upload.getStatus());
            return upload;
        });
    }

    // ------------------------------------------------------------------ success path

    @Test
    void sop_upload_is_accepted_and_published_message_uses_the_lowercase_wire_format() throws Exception {
        kafkaSendSucceeds();

        MvcResult result = mockMvc.perform(request(sopParams()))
                .andExpect(status().isAccepted())
                .andReturn();
        Map<String, Object> response = readMap(body(result));
        String uploadId = (String) response.get("uploadId");
        assertThat(response).containsEntry("status", "accepted");

        ArgumentCaptor<String> key = ArgumentCaptor.forClass(String.class);
        ArgumentCaptor<String> value = ArgumentCaptor.forClass(String.class);
        verify(kafkaTemplate).send(eq("test-topic"), key.capture(), value.capture());

        assertThat(key.getValue()).isEqualTo(uploadId); // the uploadId is the Kafka key
        Map<String, Object> message = readMap(value.getValue());
        assertThat(message)
                .containsEntry("uploadId", uploadId)
                .containsEntry("storageKey", uploadId + "-note.md") // a logical key (the file name), not a physical path
                .containsEntry("contentKind", "document") // lowercase, not DOCUMENT
                .containsEntry("docType", "sop")
                .containsEntry("department", "ALL")
                .containsEntry("source", "manual");
        assertThat(message.get("campaignId")).isNull();
        assertThat(uploadDir.resolve(uploadId + "-note.md")).exists(); // the file really was saved

        // the uploads row was written before the message, with who uploaded it and the SOP title
        ArgumentCaptor<Upload> row = ArgumentCaptor.forClass(Upload.class);
        verify(uploadRepository).save(row.capture());
        Upload upload = row.getValue();
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
        kafkaSendSucceeds();

        mockMvc.perform(request(Map.of("contentKind", "document", "docType", "campaign_review",
                        "department", "Beauty", "campaignId", "3")))
                .andExpect(status().isAccepted());

        ArgumentCaptor<String> value = ArgumentCaptor.forClass(String.class);
        verify(kafkaTemplate).send(eq("test-topic"), anyString(), value.capture());
        assertThat(readMap(value.getValue()))
                .containsEntry("docType", "campaign_review")
                .containsEntry("campaignId", 3)
                .containsEntry("department", "Beauty");
    }

    @Test
    void data_table_upload_uses_tableType_instead_of_docType() throws Exception {
        kafkaSendSucceeds();

        mockMvc.perform(request(Map.of("contentKind", "data_table", "tableType", "campaign_products",
                        "department", "Beauty", "campaignId", "3")))
                .andExpect(status().isAccepted());

        ArgumentCaptor<String> value = ArgumentCaptor.forClass(String.class);
        verify(kafkaTemplate).send(eq("test-topic"), anyString(), value.capture());
        Map<String, Object> message = readMap(value.getValue());
        assertThat(message)
                .containsEntry("contentKind", "data_table")
                .containsEntry("tableType", "campaign_products");
        assertThat(message.get("docType")).isNull();
    }

    /**
     * The file name is whatever the client sent. Joined onto the disk path as is, "../../evil.md"
     * would write outside the upload folder. This checks that a malicious name is flattened into a
     * safe single-level name, the file really lands in the upload folder, and the published
     * storageKey has no path separator (so the consumer can't escape either when it joins the path).
     */
    @Test
    void a_malicious_filename_cannot_escape_the_upload_directory() throws Exception {
        kafkaSendSucceeds();
        MockMultipartFile evil = new MockMultipartFile(
                "file", "../../evil.md", "text/markdown", "x".getBytes(UTF_8));

        mockMvc.perform(multipart("/documents/upload").file(evil)
                        .header("X-User-Id", "5").param("title", "Evil")
                        .param("contentKind", "document").param("docType", "sop").param("department", "ALL"))
                .andExpect(status().isAccepted());

        ArgumentCaptor<String> value = ArgumentCaptor.forClass(String.class);
        verify(kafkaTemplate).send(eq("test-topic"), anyString(), value.capture());
        String storageKey = (String) readMap(value.getValue()).get("storageKey");

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

    // ------------------------------------------------------------------ parameter validation

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
        verifyNoInteractions(kafkaTemplate); // a rejected request must not send anything to Kafka
        verifyNoInteractions(uploadRepository); // nor record an upload
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

        verifyNoInteractions(kafkaTemplate, uploadRepository);
        assertThat(fileCount()).isZero();
    }

    @Test
    void if_the_uploads_row_cannot_be_written_nothing_is_published_and_the_file_is_removed() throws Exception {
        when(uploadRepository.save(any(Upload.class))).thenThrow(new RuntimeException("database down"));

        mockMvc.perform(request(sopParams())).andExpect(status().isInternalServerError());

        verifyNoInteractions(kafkaTemplate);
        assertThat(fileCount()).isZero();
    }

    // ------------------------------------------------------------------ Kafka failure paths

    /**
     * Regression test. When the topic doesn't exist, KafkaTemplate.send() ITSELF throws
     * org.apache.kafka.common.errors.TimeoutException (instead of returning a failed Future).
     * Catching java.util.concurrent.TimeoutException instead -- same name, different package -- lets
     * the exception slip through as Spring's generic 500 and leaves an orphan file behind.
     */
    @Test
    void send_throwing_kafkas_own_TimeoutException_gives_503_and_cleans_up_the_file() throws Exception {
        when(kafkaTemplate.send(anyString(), anyString(), anyString()))
                .thenThrow(new org.apache.kafka.common.errors.TimeoutException(
                        "Topic test-topic not present in metadata after 5000 ms."));

        MvcResult result = mockMvc.perform(request(sopParams()))
                .andExpect(status().isServiceUnavailable())
                .andReturn();

        assertThat((String) readMap(body(result)).get("error")).contains("unavailable");
        assertThat(fileCount()).isZero(); // definitely not sent -> the orphan file must be removed
        assertThat(savedStatuses).containsExactly("received", "failed"); // written first, then marked failed
    }

    @Test
    void future_that_fails_gives_503_and_cleans_up_the_file() throws Exception {
        when(kafkaTemplate.send(anyString(), anyString(), anyString()))
                .thenReturn(CompletableFuture.failedFuture(new RuntimeException("broker down")));

        mockMvc.perform(request(sopParams()))
                .andExpect(status().isServiceUnavailable());

        assertThat(fileCount()).isZero(); // the Future completed exceptionally = certain failure -> clean up
        assertThat(savedStatuses).containsExactly("received", "failed");
    }

    /**
     * "Timeout != failure": when waiting for the send result times out, the message MAY ALREADY HAVE
     * GONE OUT and the consumer will come for the file. Deleting it then leaves "message without file",
     * worse than an orphan, so the file must stay.
     */
    @Test
    void timeout_waiting_for_the_send_result_is_ambiguous_so_the_file_is_KEPT() throws Exception {
        when(kafkaTemplate.send(anyString(), anyString(), anyString()))
                .thenReturn(new CompletableFuture<>()); // never completes

        MvcResult result = mockMvc.perform(request(sopParams()))
                .andExpect(status().isServiceUnavailable())
                .andReturn();

        assertThat((String) readMap(body(result)).get("error")).contains("outcome unknown");
        assertThat(fileCount()).isEqualTo(1); // outcome unknown -> must not delete
        assertThat(savedStatuses).containsExactly("received"); // and the row stays received: the message may still arrive
    }

    /**
     * Thread interrupted: the file stays, and THE INTERRUPT FLAG MUST BE RESTORED. When
     * InterruptedException is thrown the JVM has already cleared the thread's flag; swallowing it in the
     * catch loses the signal "someone asked this thread to stop".
     */
    @Test
    @SuppressWarnings("unchecked")
    void interruption_is_ambiguous_keeps_the_file_and_restores_the_interrupt_flag() throws Exception {
        CompletableFuture<SendResult<String, String>> future = mock(CompletableFuture.class);
        when(future.get(anyLong(), eq(TimeUnit.MILLISECONDS))).thenThrow(new InterruptedException("test"));
        when(kafkaTemplate.send(anyString(), anyString(), anyString())).thenReturn(future);

        try {
            mockMvc.perform(request(sopParams())).andExpect(status().isServiceUnavailable());

            assertThat(fileCount()).isEqualTo(1);
            assertThat(Thread.currentThread().isInterrupted())
                    .as("after catching InterruptedException, Thread.currentThread().interrupt() must restore the flag")
                    .isTrue();
        } finally {
            Thread.interrupted(); // clear the flag so later tests are unaffected
        }
    }

    // ------------------------------------------------------------------ helpers

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

    /**
     * The order matters: build the future completely first, then stub kafkaTemplate.
     * when(kafkaTemplate.send(...)).thenReturn(succeededFuture()) does not work: succeededFuture()
     * stubs other mocks with when(...) itself, and Mockito doesn't allow starting a stubbing while
     * another is unfinished (UnfinishedStubbingException).
     */
    private void kafkaSendSucceeds() {
        CompletableFuture<SendResult<String, String>> future = succeededFuture();
        when(kafkaTemplate.send(anyString(), anyString(), anyString())).thenReturn(future);
    }

    @SuppressWarnings("unchecked")
    private static CompletableFuture<SendResult<String, String>> succeededFuture() {
        RecordMetadata metadata = mock(RecordMetadata.class);
        when(metadata.partition()).thenReturn(0);
        when(metadata.offset()).thenReturn(7L);
        SendResult<String, String> result = mock(SendResult.class);
        when(result.getRecordMetadata()).thenReturn(metadata);
        return CompletableFuture.completedFuture(result);
    }
}
