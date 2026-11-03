package com.opsassistant.backend.query;

import static org.assertj.core.api.Assertions.assertThat;

import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * The request and response format between backend-service and llm-service: the single source of truth is
 * contracts/query.examples.json, and llm-service/tests/test_query_api.py checks the same file. If either side
 * quietly changes a field name, it no longer matches the file and that side's test fails.
 */
class QueryContractTest {

    private final JsonMapper json = JsonMapper.builder().build();

    private JsonNode contract() throws Exception {
        return json.readTree(Files.readString(Path.of("..", "contracts", "query.examples.json")));
    }

    @Test
    void every_request_example_is_exactly_what_java_sends() throws Exception {
        JsonNode requests = contract().get("requests");

        assertThat(requests.size()).isPositive();
        for (JsonNode example : requests) {
            assertRoundTrips(example, json.treeToValue(example, LlmQueryRequest.class));
        }
    }

    @Test
    void every_response_example_is_exactly_what_java_reads() throws Exception {
        JsonNode responses = contract().get("responses");

        assertThat(responses.size()).isPositive();
        for (JsonNode example : responses) {
            assertRoundTrips(example, json.treeToValue(example, LlmQueryResponse.class));
        }
    }

    /** Through text, so a long and an int holding the same number compare as equal. */
    private void assertRoundTrips(JsonNode example, Object parsed) {
        assertThat((Object) json.readTree(json.writeValueAsString(parsed))).isEqualTo(example);
    }
}
