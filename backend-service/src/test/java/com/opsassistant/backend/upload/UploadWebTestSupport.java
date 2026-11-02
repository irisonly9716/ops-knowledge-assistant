package com.opsassistant.backend.upload;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.AdditionalAnswers.returnsFirstArg;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.opsassistant.backend.campaign.Campaign;
import com.opsassistant.backend.campaign.CampaignRepository;
import com.opsassistant.backend.department.Department;
import com.opsassistant.backend.department.DepartmentRepository;
import com.opsassistant.backend.document.DocumentRepository;
import com.opsassistant.backend.outbox.OutboxMessage;
import com.opsassistant.backend.outbox.OutboxRepository;
import com.opsassistant.backend.user.User;
import com.opsassistant.backend.user.UserRepository;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;
import java.util.Optional;
import java.util.stream.Stream;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.io.TempDir;
import org.mockito.ArgumentCaptor;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.annotation.Import;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import tools.jackson.databind.json.JsonMapper;

/**
 * What the upload endpoint tests share. They load the web layer plus the REAL UploadService and UploadStorage,
 * and fake only the repositories: the rules live in the service, so status codes are tested through it, with
 * no Postgres or Kafka running. The queries themselves are tested in UploadFlowIntegrationTest.
 */
@Import({UploadService.class, UploadStorage.class})
abstract class UploadWebTestSupport {

    static final String STAFF = "5";
    static final String OTHER_STAFF = "10";
    static final String GROCERY_STAFF = "11";
    static final String MANAGER = "6";
    static final String GROCERY_MANAGER = "7";
    static final String EXECUTIVE = "8";
    static final String ADMIN = "9";

    static final long BEAUTY_CAMPAIGN = 3;
    static final long GROCERY_CAMPAIGN = 4;

    /** Uploaded files go to this temporary folder, never the real one. */
    @TempDir
    static Path uploadDir;

    @DynamicPropertySource
    static void properties(DynamicPropertyRegistry registry) {
        registry.add("document.upload-dir", () -> uploadDir.toString());
    }

    @Autowired
    MockMvc mockMvc;

    @MockitoBean
    UploadRepository uploadRepository;
    @MockitoBean
    OutboxRepository outboxRepository;
    @MockitoBean
    DocumentRepository documentRepository;
    @MockitoBean
    CampaignRepository campaignRepository;
    @MockitoBean
    DepartmentRepository departmentRepository;
    @MockitoBean
    DataTableRepository dataTableRepository;
    @MockitoBean
    UserRepository userRepository;

    final JsonMapper json = JsonMapper.builder().build();

    @BeforeEach
    void setUpTheWorld() throws IOException {
        try (Stream<Path> files = Files.list(uploadDir)) {
            files.forEach(p -> p.toFile().delete());   // every test starts with an empty folder
        }
        user(STAFF, "staff", 1L);
        user(OTHER_STAFF, "staff", 1L);
        user(GROCERY_STAFF, "staff", 2L);
        user(MANAGER, "manager", 1L);
        user(GROCERY_MANAGER, "manager", 2L);
        user(EXECUTIVE, "executive", null);
        user(ADMIN, "admin", null);
        when(departmentRepository.findById(1L)).thenReturn(Optional.of(new Department(1L, "Beauty")));
        when(departmentRepository.findById(2L)).thenReturn(Optional.of(new Department(2L, "Fresh Grocery")));
        when(campaignRepository.findById(BEAUTY_CAMPAIGN)).thenReturn(Optional.of(campaign(BEAUTY_CAMPAIGN, 1L)));
        when(campaignRepository.findById(GROCERY_CAMPAIGN)).thenReturn(Optional.of(campaign(GROCERY_CAMPAIGN, 2L)));
        lenient().when(uploadRepository.save(any())).then(returnsFirstArg());
    }

    private void user(String id, String role, Long departmentId) {
        long userId = Long.parseLong(id);
        when(userRepository.findById(userId)).thenReturn(Optional.of(new User(userId, role + " " + id, departmentId, role)));
    }

    private static Campaign campaign(long id, long departmentId) {
        Campaign campaign = new Campaign();
        campaign.setCampaignId(id);
        campaign.setDepartmentId(departmentId);
        return campaign;
    }

    /** The event that went into the outbox (exactly one), as its JSON fields. */
    Map<String, Object> event() {
        ArgumentCaptor<OutboxMessage> message = ArgumentCaptor.forClass(OutboxMessage.class);
        verify(outboxRepository).save(message.capture());
        return readMap(message.getValue().getPayload());
    }

    /** MockHttpServletResponse decodes as ISO-8859-1 by default; non-ASCII error messages need UTF-8. */
    Map<String, Object> bodyOf(MvcResult result) throws Exception {
        return readMap(result.getResponse().getContentAsString(java.nio.charset.StandardCharsets.UTF_8));
    }

    String errorOf(MvcResult result) throws Exception {
        return (String) bodyOf(result).get("error");
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> readMap(String text) {
        return json.readValue(text, Map.class);
    }

    long fileCount() throws IOException {
        try (Stream<Path> files = Files.list(uploadDir)) {
            return files.count();
        }
    }

    void assertFileCount(long expected) throws IOException {
        assertThat(fileCount()).isEqualTo(expected);
    }
}
