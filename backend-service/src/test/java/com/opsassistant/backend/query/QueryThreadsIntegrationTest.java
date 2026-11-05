package com.opsassistant.backend.query;

import static org.assertj.core.api.Assertions.assertThat;

import com.zaxxer.hikari.HikariDataSource;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.annotation.Import;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Requests run on virtual threads, in the real Tomcat. On Tomcat's own pool of 200 threads, 900 queries waiting for a
 * model queue up behind each other. Without that limit the wait for a database connection needs its own, or every query
 * can pile up there. Needs the docker compose Postgres.
 */
@Tag("integration")
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT, properties = "outbox.relay.enabled=false")
@Import(QueryThreadsIntegrationTest.ThreadKindEndpoint.class)
class QueryThreadsIntegrationTest {

    @TestConfiguration
    @RestController
    static class ThreadKindEndpoint {
        @GetMapping("/test/thread-is-virtual")
        boolean threadIsVirtual() {
            return Thread.currentThread().isVirtual();
        }
    }

    @Value("${local.server.port}")
    int port;
    @Autowired
    HikariDataSource dataSource;
    @MockitoBean
    KafkaTemplate<String, String> kafkaTemplate;

    @Test
    void a_request_runs_on_a_virtual_thread() throws Exception {
        try (HttpClient client = HttpClient.newHttpClient()) {
            HttpRequest request = HttpRequest.newBuilder(URI.create("http://localhost:" + port + "/test/thread-is-virtual")).build();

            assertThat(client.send(request, HttpResponse.BodyHandlers.ofString()).body()).isEqualTo("true");
        }
    }

    @Test
    void the_wait_for_a_database_connection_ends_inside_the_3_s_a_query_may_take() {
        // Checking a connection it just got comes on top of the wait for one
        assertThat(dataSource.getConnectionTimeout() + dataSource.getValidationTimeout()).isLessThanOrEqualTo(3000);
    }

    @Test
    void a_statement_can_not_wait_for_a_frozen_database_forever() {
        assertThat(dataSource.getDataSourceProperties().getProperty("socketTimeout")).isEqualTo("10");
    }
}
