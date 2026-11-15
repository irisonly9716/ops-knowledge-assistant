package com.opsassistant.backend.audit;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.opsassistant.backend.user.User;
import com.opsassistant.backend.user.UserRepository;
import java.util.Optional;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;

/** PUT /query/{queryId}/feedback: only the asker rates, and what a rating may be. The SQL: QueryAuditIntegrationTest. */
@WebMvcTest(FeedbackController.class)
class FeedbackControllerTest {

    private static final UUID MINE = UUID.fromString("7c9e6679-7425-40de-944b-e07fc1f90ae7");
    private static final UUID SOMEONE_ELSES = UUID.fromString("16fd2706-8baf-433b-82eb-8c7fada847da");

    @Autowired
    MockMvc mockMvc;
    @MockitoBean
    QueryAuditRepository audits;
    @MockitoBean
    UserRepository userRepository;

    @BeforeEach
    void setUp() {
        when(userRepository.findById(2L)).thenReturn(Optional.of(new User(2L, "Staff", 1L, "staff")));
        when(audits.askerOf(MINE)).thenReturn(Optional.of(2L));
        when(audits.askerOf(SOMEONE_ELSES)).thenReturn(Optional.of(3L));
        when(audits.askerOf(UUID.fromString("00000000-0000-0000-0000-000000000000"))).thenReturn(Optional.empty());
    }

    private static MockHttpServletRequestBuilder rate(String queryId, String body) {
        return put("/query/" + queryId + "/feedback").header("X-User-Id", "2").contentType(MediaType.APPLICATION_JSON)
                .content(body);
    }

    @Test
    void the_asker_rates_their_own_answer_and_gets_nothing_back() throws Exception {
        mockMvc.perform(rate(MINE.toString(), "{\"rating\":\"down\",\"comment\":\"  It named the wrong campaign.  \"}"))
                .andExpect(status().isNoContent())
                .andExpect(content().string(""));

        verify(audits).saveFeedback(MINE, "down", "It named the wrong campaign.");
    }

    @Test
    void a_blank_or_missing_comment_counts_as_none() throws Exception {
        mockMvc.perform(rate(MINE.toString(), "{\"rating\":\"up\",\"comment\":\"   \"}")).andExpect(status().isNoContent());
        mockMvc.perform(rate(MINE.toString(), "{\"rating\":\"up\"}")).andExpect(status().isNoContent());

        verify(audits, org.mockito.Mockito.times(2)).saveFeedback(MINE, "up", null);
    }

    @Test
    void someone_elses_answer_is_forbidden_and_nothing_is_saved() throws Exception {
        mockMvc.perform(rate(SOMEONE_ELSES.toString(), "{\"rating\":\"down\"}"))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("only the asker can rate an answer"));

        verify(audits, never()).saveFeedback(any(), anyString(), any());
    }

    @Test
    void a_question_that_was_never_recorded_is_not_found() throws Exception {
        mockMvc.perform(rate("00000000-0000-0000-0000-000000000000", "{\"rating\":\"up\"}"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error").value("unknown query: 00000000-0000-0000-0000-000000000000"));
    }

    @ParameterizedTest
    @ValueSource(strings = {"{\"rating\":\"meh\"}", "{\"rating\":\"UP\"}", "{\"rating\":\"\"}", "{}", "{\"rating\":null}"})
    void a_rating_that_isnt_up_or_down_is_a_bad_request_and_looks_nothing_up(String body) throws Exception {
        mockMvc.perform(rate(MINE.toString(), body))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("rating must be up or down"));

        verify(audits, never()).askerOf(any());
    }

    @Test
    void a_comment_is_counted_in_characters_as_postgres_counts_them() throws Exception {
        String thousand = "👎".repeat(1000);   // 1,000 characters, 2,000 Java chars
        mockMvc.perform(rate(MINE.toString(), "{\"rating\":\"down\",\"comment\":\"" + thousand + "\"}"))
                .andExpect(status().isNoContent());
        mockMvc.perform(rate(MINE.toString(), "{\"rating\":\"down\",\"comment\":\"" + thousand + "x\"}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("comment is longer than 1000 characters"));
    }

    @Test
    void a_query_id_that_isnt_a_uuid_is_a_bad_request_in_the_usual_shape() throws Exception {
        mockMvc.perform(rate("memory:2:abc", "{\"rating\":\"up\"}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("queryId is not a valid UUID"));
    }

    @Test
    void an_unknown_user_is_unauthorized_before_anything_is_looked_up() throws Exception {
        mockMvc.perform(put("/query/" + MINE + "/feedback").header("X-User-Id", "999")
                        .contentType(MediaType.APPLICATION_JSON).content("{\"rating\":\"up\"}"))
                .andExpect(status().isUnauthorized());

        verify(audits, never()).askerOf(any());
    }
}
