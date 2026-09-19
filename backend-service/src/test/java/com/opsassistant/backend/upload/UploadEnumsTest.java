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
 * 纯单元测试:不启动 Spring、不连任何外部服务,跑起来是毫秒级。
 * 这几个枚举是"Java 内部类型"和"外界约定的字符串"(数据库 CHECK 约束、Python 消费者)
 * 之间的翻译层,翻译错了不会报错、只会悄悄对不上,所以值得钉死。
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
        // 特别注意 "SOP"(大写)也要被拒绝:线上约定的写法是小写 sop,
        // 不能因为枚举常量名恰好叫 SOP 就悄悄放行
        assertThatThrownBy(() -> DocType.fromValue(raw))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("docType 只能是")
                .hasMessageContaining("收到:" + raw); // 报错信息要把用户实际传的值带回去,方便排查
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
        assertThatThrownBy(() -> TableType.fromValue("campaign_product")) // 少个 s
                .isInstanceOf(IllegalArgumentException.class).hasMessageContaining("campaign_product");
    }

    /**
     * 最重要的一个:枚举序列化进 JSON 后必须是小写下划线("campaign_plan"),
     * 不是 Java 枚举常量名("CAMPAIGN_PLAN")。这是跟 Python 消费者、数据库 CHECK 约束的
     * 跨语言约定——@JsonValue 一旦被误删,编译不报错、其它测试也可能不报错,
     * 只有下游消费者收到 "CAMPAIGN_PLAN" 才炸。
     */
    @Test
    @SuppressWarnings("unchecked")
    void event_serializes_enums_as_the_lowercase_wire_format() throws Exception {
        UploadEvent event = new UploadEvent(
                "u-1", "uploads/x.md", ContentKind.DOCUMENT, DocType.CAMPAIGN_REVIEW, null,
                "美妆运营", 3L, null, Source.MANUAL);

        JsonMapper mapper = JsonMapper.builder().build();
        Map<String, Object> wire = mapper.readValue(mapper.writeValueAsString(event), Map.class);

        assertThat(wire)
                .containsEntry("contentKind", "document")
                .containsEntry("docType", "campaign_review")
                .containsEntry("source", "manual")
                .containsEntry("campaignId", 3)
                .containsEntry("tableType", null); // 不适用的字段是显式的 null,不是缺失
    }
}
