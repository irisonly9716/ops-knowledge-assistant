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
}
