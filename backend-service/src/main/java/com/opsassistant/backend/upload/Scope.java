package com.opsassistant.backend.upload;

/** Who an SOP is for: the uploader's own department, or every department (stored as department = ALL). */
public enum Scope {
    DEPARTMENT("department"),
    ALL("all");

    private final String value;

    Scope(String value) {
        this.value = value;
    }

    public static Scope fromValue(String raw) {
        if (raw != null) {
            for (Scope scope : values()) {
                if (scope.value.equals(raw)) {
                    return scope;
                }
            }
        }
        throw new IllegalArgumentException("scope must be department or all, got: " + raw);
    }
}
