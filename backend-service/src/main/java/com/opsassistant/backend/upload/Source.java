package com.opsassistant.backend.upload;

import com.fasterxml.jackson.annotation.JsonValue;

/**
 * Who started the upload. This endpoint only serves manual uploads by staff, so it is always
 * MANUAL; AUTO is reserved for the scheduled automatic ingestion mentioned in
 * docs/architecture.md, which has no code yet.
 */
public enum Source {
    MANUAL("manual"),
    AUTO("auto");

    private final String value;

    Source(String value) {
        this.value = value;
    }

    @JsonValue
    public String value() {
        return value;
    }

    public static Source fromValue(String raw) {
        if (raw != null) {
            for (Source source : values()) {
                if (source.value.equals(raw)) {
                    return source;
                }
            }
        }
        throw new IllegalArgumentException("source must be manual or auto, got: " + raw);
    }
}
