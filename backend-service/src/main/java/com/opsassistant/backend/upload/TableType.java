package com.opsassistant.backend.upload;

import com.fasterxml.jackson.annotation.JsonValue;

/** Structured tables uploaded by ops staff; the two tables created in db/migration/. */
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
                "tableType must be campaign_products / campaign_customer_behavior, got: " + raw);
    }
}
