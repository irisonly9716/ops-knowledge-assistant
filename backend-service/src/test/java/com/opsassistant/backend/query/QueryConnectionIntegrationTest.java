package com.opsassistant.backend.query;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;

import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

/**
 * A query must not hold a database connection while it waits for llm-service, or the size of the connection pool
 * becomes the limit on queries in flight. It did, through Spring Boot's default spring.jpa.open-in-view=true.
 *
 * Here the pool has 2 connections and gives up after 1 s, and llm-service takes 1.5 s. If connections were held,
 * the third of 6 queries at once couldn't get one and would fail. Needs the docker compose Postgres and the mock data.
 */
@Tag("integration")
@SpringBootTest(properties = {
        "outbox.relay.enabled=false",
        "spring.datasource.hikari.maximum-pool-size=2",
        "spring.datasource.hikari.connection-timeout=1000",
})
@AutoConfigureMockMvc
class QueryConnectionIntegrationTest {

    private static final HttpServer stub = startStub();

    private static HttpServer startStub() {
        try {
            HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            server.setExecutor(Executors.newCachedThreadPool());
            server.createContext("/query", exchange -> {
                exchange.getRequestBody().readAllBytes();
                try {
                    Thread.sleep(1500);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                }
                byte[] body = "{\"status\":\"refused\",\"answer\":\"no\",\"citations\":[],\"searchedDepartments\":[\"Beauty\",\"ALL\"]}"
                        .getBytes(StandardCharsets.UTF_8);
                exchange.getResponseHeaders().add("Content-Type", "application/json");
                exchange.sendResponseHeaders(200, body.length);
                exchange.getResponseBody().write(body);
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
        registry.add("llm-service.internal-token", () -> "test-internal-token");
    }

    @AfterAll
    static void stopStub() {
        stub.stop(0);
    }

    @Autowired
    MockMvc mockMvc;
    @MockitoBean
    KafkaTemplate<String, String> kafkaTemplate;

    @Test
    void waiting_for_llm_service_holds_no_database_connection() throws Exception {
        try (ExecutorService pool = Executors.newFixedThreadPool(6)) {
            Callable<Integer> query = () -> mockMvc.perform(post("/query").header("X-User-Id", "2")
                            .contentType(MediaType.APPLICATION_JSON).content("{\"question\":\"Why?\"}"))
                    .andReturn().getResponse().getStatus();
            List<Future<Integer>> results = new ArrayList<>();
            for (int i = 0; i < 6; i++) {
                results.add(pool.submit(query));
            }
            for (Future<Integer> result : results) {
                assertThat(result.get()).isEqualTo(200);
            }
        }
    }
}
