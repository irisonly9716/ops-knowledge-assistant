package com.opsassistant.backend.query;

import java.net.http.HttpClient;
import java.time.Duration;
import java.util.Map;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientResponseException;
import org.springframework.web.server.ResponseStatusException;

/**
 * Calls llm-service. Both timeouts are set: without them a hung service would hold a request thread forever.
 * The read timeout stays above llm-service's own budget for a query, so its 504 with the reason arrives first
 * and this one only fires when the service itself is stuck.
 *
 * A 401 from llm-service is never passed on: to a client it would mean "you are unknown", while it really means
 * the two services disagree about the token, which the client can do nothing about.
 */
@Slf4j
@Component
public class QueryClient {

    private static final String TOKEN_HEADER = "X-Internal-Token";

    private final RestClient restClient;
    private final String token;

    public QueryClient(@Value("${llm-service.base-url}") String baseUrl,
                       @Value("${llm-service.internal-token}") String token,
                       @Value("${llm-service.connect-timeout-ms}") long connectTimeoutMs,
                       @Value("${llm-service.read-timeout-ms}") long readTimeoutMs) {
        // HTTP/1.1 explicitly: over plain HTTP the JDK client would otherwise ask every time to upgrade to HTTP/2
        HttpClient httpClient = HttpClient.newBuilder()
                .version(HttpClient.Version.HTTP_1_1)
                .connectTimeout(Duration.ofMillis(connectTimeoutMs))
                .build();
        JdkClientHttpRequestFactory requestFactory = new JdkClientHttpRequestFactory(httpClient);
        requestFactory.setReadTimeout(Duration.ofMillis(readTimeoutMs));
        this.restClient = RestClient.builder().baseUrl(baseUrl).requestFactory(requestFactory).build();
        this.token = token;
    }

    public LlmQueryResponse ask(LlmQueryRequest request) {
        if (token.isBlank()) {
            log.error("INTERNAL_SERVICE_TOKEN is not set, so /query can't reach llm-service");
            throw error(HttpStatus.SERVICE_UNAVAILABLE, "the answer service is not configured");
        }
        try {
            return restClient.post()
                    .uri("/query")
                    .contentType(MediaType.APPLICATION_JSON)
                    .header(TOKEN_HEADER, token)
                    .body(request)
                    .retrieve()
                    .body(LlmQueryResponse.class);
        } catch (RestClientResponseException e) {
            throw fromService(e);
        } catch (ResourceAccessException e) {
            // No HTTP answer at all. Log the cause, but never pass it on: it names internal hosts and ports
            log.warn("llm-service unreachable: {}", e.getMessage());
            boolean timedOut = e.getCause() != null && e.getCause().getClass().getName().contains("Timeout");
            throw timedOut
                    ? error(HttpStatus.GATEWAY_TIMEOUT, "the answer service did not respond in time")
                    : error(HttpStatus.SERVICE_UNAVAILABLE, "the answer service is unavailable");
        }
    }

    private static ResponseStatusException fromService(RestClientResponseException e) {
        int status = e.getStatusCode().value();
        if (status == 401 || status == 403) {
            log.error("llm-service refused the internal token: INTERNAL_SERVICE_TOKEN differs between the two services");
            return error(HttpStatus.BAD_GATEWAY, "the answer service is not configured");
        }
        HttpStatus mapped = switch (status) {
            case 400, 429, 503, 504 -> HttpStatus.valueOf(status);
            default -> HttpStatus.BAD_GATEWAY;
        };
        String retryAfter = e.getResponseHeaders() == null ? null : e.getResponseHeaders().getFirst(HttpHeaders.RETRY_AFTER);
        return retryAfter == null ? error(mapped, reasonIn(e)) : new TryAgainLaterException(mapped, reasonIn(e), retryAfter);
    }

    private static String reasonIn(RestClientResponseException e) {
        try {
            Map<?, ?> body = e.getResponseBodyAs(Map.class);
            if (body != null && body.get("error") instanceof String reason) {
                return reason;
            }
        } catch (RuntimeException ignored) {
            // Not the JSON we expected, such as a proxy's error page: fall through to the generic reason
        }
        return "the answer service failed";
    }

    private static ResponseStatusException error(HttpStatus status, String reason) {
        return new ResponseStatusException(status, reason);
    }
}
