package com.opsassistant.backend.upload;

import static org.assertj.core.api.Assertions.assertThat;

import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * Contract test: the single source of truth for the Kafka message format between backend-service
 * (Java, the producer) and llm-service (Python, the consumer) is contracts/upload-event.examples.json.
 *
 * Both sides' tests validate against THE SAME FILE:
 *   - here: every example, deserialized into UploadEvent and serialized back, must equal the original
 *     exactly (field names, lowercase enum values, explicit nulls), so what Java sends is exactly
 *     what the contract says
 *   - the Python side (llm-service/tests/test_events.py): the same examples must parse with the
 *     pydantic model
 * If either side quietly changes the format (a field name, the case of an enum value), it no longer
 * matches this file and that side's test fails. More reliable than two separate definitions kept in
 * sync by memory.
 */
class UploadEventContractTest {

    // Maven/surefire runs in backend-service/; the contract file is in contracts/ at the repository root
    private static final Path CONTRACT = Path.of("..", "contracts", "upload-event.examples.json");

    private final JsonMapper mapper = JsonMapper.builder().build();

    @Test
    void every_contract_example_survives_a_java_round_trip_unchanged() throws Exception {
        JsonNode examples = mapper.readTree(Files.readString(CONTRACT));
        assertThat(examples.size()).as("the contract file must contain examples").isGreaterThanOrEqualTo(3);

        for (JsonNode example : examples) {
            UploadEvent event = mapper.readValue(example.toString(), UploadEvent.class);
            JsonNode reserialized = mapper.readTree(mapper.writeValueAsString(event));

            assertThat(reserialized)
                    .as("example %s must equal the contract after a Java round trip", example.get("uploadId"))
                    .isEqualTo(example);
        }
    }
}
