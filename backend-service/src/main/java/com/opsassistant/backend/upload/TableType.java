package com.opsassistant.backend.upload;

import com.fasterxml.jackson.annotation.JsonValue;

/** 运营上传的结构化表格,对应 sql/02_schema.sql 里的两张表。 */
public enum TableType {
    CAMPAIGN_PRODUCTS("campaign_products"),
    CAMPAIGN_CUSTOMER_BEHAVIOR("campaign_customer_behavior");

    private final String value;

    TableType(String value) {
        this.value = value;
    }

    @JsonValue
    public String value() {
        return value;
    }

    public static TableType fromValue(String raw) {
        if (raw != null) {
            for (TableType type : values()) {
                if (type.value.equals(raw)) {
                    return type;
                }
            }
        }
        throw new IllegalArgumentException(
                "tableType 只能是 campaign_products / campaign_customer_behavior,收到:" + raw);
    }
}
