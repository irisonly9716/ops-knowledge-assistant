package com.opsassistant.backend.query;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.opsassistant.backend.department.Department;
import com.opsassistant.backend.department.DepartmentRepository;
import com.opsassistant.backend.user.User;
import com.opsassistant.backend.user.UserRepository;
import java.util.List;
import java.util.Optional;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.http.MediaType;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;

/** The limit as a client sees it: only real questions use it up, and a refusal says when to come back. */
@WebMvcTest(controllers = QueryController.class, properties = "query.rate-limit-qps=0.001")
@Import(QueryRateLimiter.class)
class QueryRateLimitTest {

    private static final String QUESTION = "{\"question\":\"Why?\"}";

    @Autowired
    MockMvc mockMvc;
    @Autowired
    QueryRateLimiter limiter;
    @MockitoBean
    QueryClient queryClient;
    @MockitoBean
    UserRepository userRepository;
    @MockitoBean
    DepartmentRepository departmentRepository;

    @BeforeEach
    void setUp() {
        when(userRepository.findById(2L)).thenReturn(Optional.of(new User(2L, "Staff", 1L, "staff")));
        when(departmentRepository.findById(1L)).thenReturn(Optional.of(new Department(1L, "Beauty")));
        when(queryClient.ask(any())).thenReturn(new LlmQueryResponse("refused", "no", List.of(), List.of("Beauty", "ALL"),
                List.of("documents"), null));
    }

    private static MockHttpServletRequestBuilder ask(String user, String body) {
        return post("/query").header("X-User-Id", user).contentType(MediaType.APPLICATION_JSON).content(body);
    }

    @Test
    void over_the_limit_is_a_429_that_says_when_to_come_back_and_nothing_is_sent() throws Exception {
        // At this rate the bucket holds a thousandth of a token, so even the first query is over the limit
        mockMvc.perform(ask("2", QUESTION))
                .andExpect(status().isTooManyRequests())
                .andExpect(header().string("Retry-After", String.valueOf(limiterWait())))
                .andExpect(jsonPath("$.error").value("too many questions right now; try again shortly"));

        verify(queryClient, never()).ask(any());
        verify(departmentRepository, never()).findById(any());   // no database work for a refused query either
    }

    @Test
    void an_unknown_user_gets_401_not_429() throws Exception {
        mockMvc.perform(ask("99", QUESTION)).andExpect(status().isUnauthorized());
    }

    @Test
    void a_malformed_question_gets_400_not_429() throws Exception {
        mockMvc.perform(ask("2", "{\"question\":\"  \"}")).andExpect(status().isBadRequest());
    }

    private long limiterWait() {
        long wait = limiter.acquireOrSecondsToWait();
        assertThat(wait).isGreaterThan(0);
        return wait;
    }
}
