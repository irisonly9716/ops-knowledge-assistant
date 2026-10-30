package com.opsassistant.backend.upload;

import com.fasterxml.jackson.annotation.JsonValue;

/**
 * Matches the CHECK constraints on documents.doc_type and uploads.doc_type in db/migration/
 * (IN ('sop','campaign_plan','campaign_review')). The three values must be exactly those of the
 * constraint: when the constraint changes, change this too, and the other way round; an enum does
 * not sync itself.
 */
public enum DocType {
    SOP("sop"),
    CAMPAIGN_PLAN("campaign_plan"),
    CAMPAIGN_REVIEW("campaign_review");

    private final String value;

    DocType(String value) {
        this.value = value;
    }

    @JsonValue
    public String value() {
        return value;
    }

    /**
     * Whether a campaign is required. Keeping this business rule on the enum itself (instead of
     * !"sop".equals(docType) in UploadController) means a new document type gets the rule written
     * together with its constant and it can't be forgotten.
     */
    public boolean needsCampaign() {
        return this != SOP;
    }

    public static DocType fromValue(String raw) {
        if (raw != null) {
            for (DocType type : values()) {
                if (type.value.equals(raw)) {
                    return type;
                }
            }
        }
        throw new IllegalArgumentException(
                "docType must be sop / campaign_plan / campaign_review, got: " + raw);
    }
}
