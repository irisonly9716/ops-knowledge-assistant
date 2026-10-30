package com.opsassistant.backend.upload;

import com.fasterxml.jackson.annotation.JsonValue;

/**
 * The two kinds of uploaded content, per the "two ingestion pipelines" design in
 * docs/architecture.md: DOCUMENT goes through RAG (chunking + embedding), DATA_TABLE is parsed
 * straight into structured rows.
 */
public enum ContentKind {
    DOCUMENT("document"),
    DATA_TABLE("data_table");

    private final String value;

    ContentKind(String value) {
        this.value = value;
    }

    /**
     * @JsonValue tells Jackson to serialize this enum as the string value() returns (lowercase,
     * underscores) rather than the constant name (DOCUMENT/DATA_TABLE, uppercase). The JSON on
     * Kafka stays the "document"/"data_table" the Python side knows; the enum only makes the Java
     * side safer and leaves the agreed string format alone.
     */
    @JsonValue
    public String value() {
        return value;
    }

    /** Convert the raw string from the request into the enum; throw with a clear reason if nothing matches. */
    public static ContentKind fromValue(String raw) {
        if (raw != null) {
            for (ContentKind kind : values()) {
                if (kind.value.equals(raw)) {
                    return kind;
                }
            }
        }
        throw new IllegalArgumentException("contentKind must be document or data_table, got: " + raw);
    }
}
