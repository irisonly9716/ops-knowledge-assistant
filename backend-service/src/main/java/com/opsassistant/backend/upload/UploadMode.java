package com.opsassistant.backend.upload;

/** What the uploader says the upload is. The system checks it against what exists instead of guessing. */
public enum UploadMode {
    NEW("new"),
    REPLACE("replace");

    private final String value;

    UploadMode(String value) {
        this.value = value;
    }

    public static UploadMode fromValue(String raw) {
        if (raw != null) {
            for (UploadMode mode : values()) {
                if (mode.value.equals(raw)) {
                    return mode;
                }
            }
        }
        throw new IllegalArgumentException("mode must be new or replace, got: " + raw);
    }
}
