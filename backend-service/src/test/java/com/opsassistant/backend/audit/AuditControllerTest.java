package com.opsassistant.backend.audit;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.opsassistant.backend.user.User;
import com.opsassistant.backend.user.UserRepository;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

/** GET /audit/queries: admin only, and how a page is asked for. The SQL: QueryAuditIntegrationTest. */
@WebMvcTest(AuditController.class)
class AuditControllerTest {

    @Autowired
    MockMvc mockMvc;
    @MockitoBean
    QueryAuditRepository audits;
    @MockitoBean
    UserRepository userRepository;

    private final AuditEntry entry = new AuditEntry(UUID.fromString("7c9e6679-7425-40de-944b-e07fc1f90ae7"),
            Instant.parse("2026-11-14T09:30:00.123456Z"), 25L, "executive", null, true, null, "answered", null, "data",
            "model", 0.98f, List.of("data"), List.of("documents"), "data", "What did Beauty make last month?", null,
            "SELECT 1", null, 1, 1, List.of(), 4, 2100, 160, "{\"router\": [300, 25]}", "0.00000015", 2412,
            "{\"route\": 1210}", "down", "Wrong month.");

    @BeforeEach
    void users() {
        when(userRepository.findById(2L)).thenReturn(Optional.of(new User(2L, "Staff", 1L, "staff")));
        when(userRepository.findById(1L)).thenReturn(Optional.of(new User(1L, "Manager", 1L, "manager")));
        when(userRepository.findById(25L)).thenReturn(Optional.of(new User(25L, "CEO", null, "executive")));
        when(userRepository.findById(24L)).thenReturn(Optional.of(new User(24L, "Admin", null, "admin")));
    }

    @ParameterizedTest
    @ValueSource(strings = {"2", "1", "25"})
    void nobody_but_admin_reads_the_audit_not_even_the_executive(String user) throws Exception {
        mockMvc.perform(get("/audit/queries").header("X-User-Id", user))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("only admin reads the audit"));

        verify(audits, never()).list(any());
    }

    @Test
    void admin_reads_the_newest_fifty_by_default() throws Exception {
        when(audits.list(new QueryAuditRepository.Filter(null, null, null, 50))).thenReturn(List.of(entry));

        mockMvc.perform(get("/audit/queries").header("X-User-Id", "24"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].queryId").value("7c9e6679-7425-40de-944b-e07fc1f90ae7"))
                .andExpect(jsonPath("$[0].createdAt").value("2026-11-14T09:30:00.123456Z"))
                .andExpect(jsonPath("$[0].crossDepartment").value(true))
                .andExpect(jsonPath("$[0].tokens.router[0]").value(300))   // the stored JSON, not a string of it
                .andExpect(jsonPath("$[0].timingsMs.route").value(1210))
                .andExpect(jsonPath("$[0].costUsd").value("0.00000015"))    // text: no 1.5E-7 on the way
                .andExpect(jsonPath("$[0].rating").value("down"));
    }

    @Test
    void the_filters_and_the_page_reach_the_query_as_asked() throws Exception {
        mockMvc.perform(get("/audit/queries").header("X-User-Id", "24").param("userId", "25")
                        .param("crossDepartment", "true").param("before", "2026-11-14T09:30:00.123456Z").param("limit", "200"))
                .andExpect(status().isOk());

        verify(audits).list(new QueryAuditRepository.Filter(25L, true,
                OffsetDateTime.parse("2026-11-14T09:30:00.123456Z"), 200));
    }

    @ParameterizedTest
    @CsvSource(delimiter = '|', value = {
            "limit|0|limit must be between 1 and 200",
            "limit|201|limit must be between 1 and 200",
            "before|yesterday|before must be a time like 2026-11-14T09:30:00Z",
            "crossDepartment|maybe|crossDepartment is not a valid Boolean",
            "userId|two|userId is not a valid Long"})
    void a_filter_that_doesnt_parse_is_a_bad_request_that_names_it(String name, String value, String error)
            throws Exception {
        mockMvc.perform(get("/audit/queries").header("X-User-Id", "24").param(name, value))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value(error));

        verify(audits, never()).list(any());
    }
}
