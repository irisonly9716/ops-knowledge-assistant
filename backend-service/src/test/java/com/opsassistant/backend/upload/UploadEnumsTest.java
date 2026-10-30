package com.opsassistant.backend.upload;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.Map;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.junit.jupiter.params.provider.NullSource;
import org.junit.jupiter.params.provider.ValueSource;
import tools.jackson.databind.json.JsonMapper;

/**
 * Plain unit tests: no Spring, no external services, milliseconds to run.
 * These enums translate between Java's internal types and the strings agreed with the outside
 * (database CHECK constraints, the Python consumer). A wrong translation raises no error, it just
 * silently stops matching, so it is worth pinning down.
 */
class UploadEnumsTest {

    @ParameterizedTest
    @CsvSource({"sop,SOP", "campaign_plan,CAMPAIGN_PLAN", "campaign_review,CAMPAIGN_REVIEW"})
    void docType_fromValue_parses_every_known_value(String raw, DocType expected) {
        assertThat(DocType.fromValue(raw)).isEqualTo(expected);
    }

    @ParameterizedTest
    @ValueSource(strings = {"campain_plan", "SOP", "Sop", "", " sop", "sop "})
    @NullSource
    void docType_fromValue_rejects_typos_case_variants_and_null(String raw) {
        // Note that "SOP" (uppercase) must be rejected too: the wire format is lowercase sop,
        // and the enum constant happening to be named SOP must not let it through
        assertThatThrownBy(() -> DocType.fromValue(raw))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("docType must be")
                .hasMessageContaining("got: " + raw); // the message echoes the value actually sent, for troubleshooting
    }

    @Test
    void only_sop_can_go_without_a_campaign() {
        assertThat(DocType.SOP.needsCampaign()).isFalse();
        assertThat(DocType.CAMPAIGN_PLAN.needsCampaign()).isTrue();
        assertThat(DocType.CAMPAIGN_REVIEW.needsCampaign()).isTrue();
    }

    @Test
    void contentKind_and_tableType_reject_unknown_values() {
        assertThat(ContentKind.fromValue("document")).isEqualTo(ContentKind.DOCUMENT);
        assertThat(ContentKind.fromValue("data_table")).isEqualTo(ContentKind.DATA_TABLE);
        assertThatThrownBy(() -> ContentKind.fromValue("video"))
                .isInstanceOf(IllegalArgumentException.class).hasMessageContaining("video");

        assertThat(TableType.fromValue("campaign_products")).isEqualTo(TableType.CAMPAIGN_PRODUCTS);
        assertThat(TableType.fromValue("campaign_customer_behavior")).isEqualTo(TableType.CAMPAIGN_CUSTOMER_BEHAVIOR);
        assertThatThrownBy(() -> TableType.fromValue("campaign_product")) // missing an s
                .isInstanceOf(IllegalArgumentException.class).hasMessageContaining("campaign_product");
    }

    /**
     * The most important one: serialized to JSON, an enum must be lowercase with underscores
     * ("campaign_plan"), not the Java constant name ("CAMPAIGN_PLAN"). That is the cross-language
     * agreement with the Python consumer and the database CHECK constraints. If @JsonValue is
     * deleted by mistake, nothing fails to compile and other tests may stay green; only the
     * downstream consumer blows up when it receives "CAMPAIGN_PLAN".
     */
    @Test
    @SuppressWarnings("unchecked")
    void event_serializes_enums_as_the_lowercase_wire_format() throws Exception {
        UploadEvent event = new UploadEvent(
                "u-1", "u-1-x.md", ContentKind.DOCUMENT, DocType.CAMPAIGN_REVIEW, null,
                "Beauty", 1L, null, Source.MANUAL);

        JsonMapper mapper = JsonMapper.builder().build();
        Map<String, Object> wire = mapper.readValue(mapper.writeValueAsString(event), Map.class);

        assertThat(wire)
                .containsEntry("contentKind", "document")
                .containsEntry("docType", "campaign_review")
                .containsEntry("source", "manual")
                .containsEntry("campaignId", 1)
                .containsEntry("tableType", null); // a field that does not apply is an explicit null, not missing
    }
}
