package com.opsassistant.backend.query;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.opsassistant.backend.department.Department;
import com.opsassistant.backend.department.DepartmentRepository;
import com.opsassistant.backend.user.User;
import com.opsassistant.backend.user.UserRepository;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.Executors;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.dao.DataAccessResourceFailureException;
import org.springframework.http.MediaType;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;
import org.springframework.transaction.CannotCreateTransactionException;
import tools.jackson.databind.json.JsonMapper;

/**
 * POST /query with the real QueryClient talking over a real socket to a stub llm-service, so timeouts, refused
 * connections and error bodies are the real thing and not a mock's idea of them. The stub answers on its own
 * thread pool, and a test sets what it does next.
 */
@WebMvcTest(QueryController.class)
@Import({QueryClient.class, QueryRateLimiter.class})
class QueryControllerTest {

    private static final String TOKEN = "test-internal-token";
    private static final String ANSWER = """
            {"status":"answered","answer":"Do this [1].","citations":[{"number":1,"docId":3,"docType":"sop",
             "title":"Account Restriction Handling","department":"ALL","headingPath":["Steps"],"score":0.51}],
             "searchedDepartments":["Beauty","ALL"]}""";
    private static final HttpServer stub = startStub();

    private static volatile int nextStatus;
    private static volatile String nextBody;
    private static volatile long nextDelayMs;
    private static volatile String nextRetryAfter;
    private static volatile int calls;
    private static volatile String lastBody;
    private static volatile String lastToken;
    private static volatile String lastPath;

    private static HttpServer startStub() {
        try {
            HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            server.setExecutor(Executors.newCachedThreadPool());
            server.createContext("/", exchange -> {
                calls++;
                lastPath = exchange.getRequestURI().getPath();
                lastToken = exchange.getRequestHeaders().getFirst("X-Internal-Token");
                lastBody = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
                try {
                    Thread.sleep(nextDelayMs);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                }
                byte[] response = nextBody.getBytes(StandardCharsets.UTF_8);
                exchange.getResponseHeaders().add("Content-Type", "application/json");
                if (nextRetryAfter != null) {
                    exchange.getResponseHeaders().add("Retry-After", nextRetryAfter);
                }
                exchange.sendResponseHeaders(nextStatus, response.length);
                exchange.getResponseBody().write(response);
                exchange.close();
            });
            server.start();
            return server;
        } catch (IOException e) {
            throw new IllegalStateException(e);
        }
    }

    @DynamicPropertySource
    static void properties(DynamicPropertyRegistry registry) {
        registry.add("llm-service.base-url", () -> "http://127.0.0.1:" + stub.getAddress().getPort());
        registry.add("llm-service.internal-token", () -> TOKEN);
        registry.add("llm-service.read-timeout-ms", () -> "300");
    }

    @AfterAll
    static void stopStub() {
        stub.stop(0);
    }

    @Autowired
    MockMvc mockMvc;
    @MockitoBean
    UserRepository userRepository;
    @MockitoBean
    DepartmentRepository departmentRepository;

    private final JsonMapper json = JsonMapper.builder().build();

    @BeforeEach
    void setUp() {
        nextStatus = 200;
        nextBody = ANSWER;
        nextDelayMs = 0;
        nextRetryAfter = null;
        calls = 0;
        lastBody = null;
        lastToken = null;
        when(userRepository.findById(2L)).thenReturn(Optional.of(new User(2L, "Staff", 1L, "staff")));
        when(userRepository.findById(1L)).thenReturn(Optional.of(new User(1L, "Manager", 1L, "manager")));
        when(userRepository.findById(25L)).thenReturn(Optional.of(new User(25L, "CEO", null, "executive")));
        when(userRepository.findById(24L)).thenReturn(Optional.of(new User(24L, "Admin", null, "admin")));
        when(departmentRepository.findById(1L)).thenReturn(Optional.of(new Department(1L, "Beauty")));
    }

    private static MockHttpServletRequestBuilder ask(String user, String body) {
        return post("/query").header("X-User-Id", user).contentType(MediaType.APPLICATION_JSON).content(body);
    }

    private static MockHttpServletRequestBuilder ask(String user) {
        return ask(user, "{\"question\":\"How do I handle a restriction?\"}");
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> sent() {
        return json.readValue(lastBody, Map.class);
    }

    // ------------------------------------------------------------------ who is asking

    @Test
    void the_answer_of_llm_service_comes_back_as_it_is() throws Exception {
        mockMvc.perform(ask("2"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("answered"))
                .andExpect(jsonPath("$.answer").value("Do this [1]."))
                .andExpect(jsonPath("$.citations[0].number").value(1))
                .andExpect(jsonPath("$.citations[0].docId").value(3))
                .andExpect(jsonPath("$.citations[0].title").value("Account Restriction Handling"))
                .andExpect(jsonPath("$.citations[0].headingPath[0]").value("Steps"))
                .andExpect(jsonPath("$.searchedDepartments[0]").value("Beauty"));
    }

    @Test
    void the_role_and_department_come_from_the_users_table() throws Exception {
        mockMvc.perform(ask("2")).andExpect(status().isOk());

        assertThat(lastPath).isEqualTo("/query");
        assertThat(sent())
                .containsEntry("question", "How do I handle a restriction?")
                .containsEntry("userId", 2)
                .containsEntry("role", "staff")
                .containsEntry("department", "Beauty");
    }

    @Test
    void a_client_can_not_choose_its_own_department_or_role() throws Exception {
        mockMvc.perform(ask("2", """
                        {"question":"Why?","department":"Fresh Grocery","role":"executive","userId":25}"""))
                .andExpect(status().isOk());

        assertThat(sent())
                .containsEntry("userId", 2)
                .containsEntry("role", "staff")
                .containsEntry("department", "Beauty");
    }

    @ParameterizedTest
    @CsvSource({"25, executive", "24, admin"})
    void executive_and_admin_are_sent_with_no_department(String user, String role) throws Exception {
        mockMvc.perform(ask(user)).andExpect(status().isOk());

        assertThat(sent()).containsEntry("role", role).containsEntry("department", null);
    }

    @Test
    void every_call_carries_the_internal_token() throws Exception {
        mockMvc.perform(ask("2")).andExpect(status().isOk());

        assertThat(lastToken).isEqualTo(TOKEN);
    }

    @ParameterizedTest
    @CsvSource({"99", "abc"})
    void an_unknown_user_gets_401_and_nothing_is_sent(String user) throws Exception {
        mockMvc.perform(ask(user)).andExpect(status().isUnauthorized());

        assertThat(calls).isZero();
    }

    @Test
    void no_user_at_all_gets_401() throws Exception {
        mockMvc.perform(post("/query").contentType(MediaType.APPLICATION_JSON).content("{\"question\":\"Why?\"}"))
                .andExpect(status().isUnauthorized());

        assertThat(calls).isZero();
    }

    // ------------------------------------------------------------------ what it accepts

    @ParameterizedTest
    @CsvSource(delimiter = '|', value = {"{}", "{\"question\":null}", "{\"question\":\"\"}", "{\"question\":\"   \"}"})
    void a_missing_question_is_a_bad_request_and_nothing_is_sent(String body) throws Exception {
        mockMvc.perform(ask("2", body))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("question is required"));

        assertThat(calls).isZero();
    }

    @Test
    void a_question_that_is_too_long_is_refused_here() throws Exception {
        String tooLong = "x".repeat(QueryController.MAX_QUESTION_CHARS + 1);
        mockMvc.perform(ask("2", "{\"question\":\"" + tooLong + "\"}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("question is longer than 2000 characters"));

        assertThat(calls).isZero();
    }

    @Test
    void the_longest_allowed_question_goes_through() throws Exception {
        String longest = "x".repeat(QueryController.MAX_QUESTION_CHARS);
        mockMvc.perform(ask("2", "{\"question\":\"" + longest + "\"}")).andExpect(status().isOk());
    }

    @Test
    void spaces_around_a_question_are_dropped() throws Exception {
        mockMvc.perform(ask("2", "{\"question\":\"  Why?  \"}")).andExpect(status().isOk());

        assertThat(sent()).containsEntry("question", "Why?");
    }

    @Test
    void malformed_json_is_a_bad_request_with_the_usual_shape() throws Exception {
        mockMvc.perform(ask("2", "{not json"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("the request body is not valid JSON"));

        assertThat(calls).isZero();
    }

    // ------------------------------------------------------------------ when llm-service fails

    @ParameterizedTest
    @CsvSource(delimiter = '|', value = {
            "504 | no answer within 12 s | 504",
            "503 | the document search is unavailable | 503",
            "400 | question: too long | 400",
            "429 | too many queries | 429",
            "502 | the model's answer failed validation | 502",
            "500 | internal error | 502",
    })
    void a_failure_keeps_its_reason_and_a_status_that_fits(int status, String reason, int expected) throws Exception {
        nextStatus = status;
        nextBody = "{\"error\":\"" + reason + "\"}";

        mockMvc.perform(ask("2"))
                .andExpect(status().is(expected))
                .andExpect(jsonPath("$.error").value(reason));
    }

    @Test
    void a_database_connection_that_does_not_come_in_time_is_a_503_in_the_usual_shape() throws Exception {
        when(userRepository.findById(2L)).thenThrow(new CannotCreateTransactionException("no connection in time"));

        mockMvc.perform(ask("2"))
                .andExpect(status().isServiceUnavailable())
                .andExpect(jsonPath("$.error").value("the database is unavailable; try again shortly"));
    }

    @Test
    void a_statement_that_gets_no_answer_from_the_database_is_a_503_in_the_usual_shape() throws Exception {
        when(userRepository.findById(2L)).thenThrow(new DataAccessResourceFailureException("no answer in time"));

        mockMvc.perform(ask("2"))
                .andExpect(status().isServiceUnavailable())
                .andExpect(jsonPath("$.error").value("the database is unavailable; try again shortly"));
    }

    @Test
    void when_llm_service_says_when_to_come_back_the_client_hears_it() throws Exception {
        nextStatus = 503;
        nextBody = "{\"error\":\"every model call slot is taken\"}";
        nextRetryAfter = "7";

        mockMvc.perform(ask("2"))
                .andExpect(status().isServiceUnavailable())
                .andExpect(header().string("Retry-After", "7"))
                .andExpect(jsonPath("$.error").value("every model call slot is taken"));
    }

    @Test
    void a_failure_without_a_time_to_come_back_invents_none() throws Exception {
        nextStatus = 503;
        nextBody = "{\"error\":\"the document search is unavailable\"}";

        mockMvc.perform(ask("2"))
                .andExpect(status().isServiceUnavailable())
                .andExpect(header().doesNotExist("Retry-After"));
    }

    @ParameterizedTest
    @CsvSource({"401", "403"})
    void a_refused_token_is_a_problem_between_the_services_not_an_unknown_user(int status) throws Exception {
        nextStatus = status;
        nextBody = "{\"error\":\"this endpoint only accepts calls from the backend\"}";

        mockMvc.perform(ask("2"))
                .andExpect(status().isBadGateway())
                .andExpect(jsonPath("$.error").value("the answer service is not configured"));
    }

    @Test
    void an_error_that_is_not_our_json_still_has_the_usual_shape() throws Exception {
        nextStatus = 502;
        nextBody = "<html>Bad gateway</html>";

        mockMvc.perform(ask("2"))
                .andExpect(status().isBadGateway())
                .andExpect(jsonPath("$.error").value("the answer service failed"));
    }

    @Test
    void a_service_that_hangs_is_cut_off_by_the_read_timeout() throws Exception {
        nextDelayMs = 1500;

        long started = System.nanoTime();
        mockMvc.perform(ask("2"))
                .andExpect(status().isGatewayTimeout())
                .andExpect(jsonPath("$.error").value("the answer service did not respond in time"));

        assertThat((System.nanoTime() - started) / 1_000_000).isLessThan(1400);
    }

    @Test
    void a_service_that_is_down_is_a_503_that_does_not_name_internal_addresses() throws Exception {
        QueryClient down = new QueryClient("http://127.0.0.1:1", TOKEN, 500, 500);

        org.assertj.core.api.Assertions.assertThatThrownBy(
                        () -> down.ask(new LlmQueryRequest("Why?", 2L, "staff", "Beauty")))
                .isInstanceOfSatisfying(org.springframework.web.server.ResponseStatusException.class, e -> {
                    assertThat(e.getStatusCode().value()).isEqualTo(503);
                    assertThat(e.getReason()).isEqualTo("the answer service is unavailable");
                });
    }

    @Test
    void an_unset_token_is_a_503_and_nothing_is_sent() {
        QueryClient unconfigured = new QueryClient("http://127.0.0.1:" + stub.getAddress().getPort(), "", 500, 500);

        org.assertj.core.api.Assertions.assertThatThrownBy(
                        () -> unconfigured.ask(new LlmQueryRequest("Why?", 2L, "staff", "Beauty")))
                .isInstanceOfSatisfying(org.springframework.web.server.ResponseStatusException.class,
                        e -> assertThat(e.getStatusCode().value()).isEqualTo(503));
        assertThat(calls).isZero();
    }
}
