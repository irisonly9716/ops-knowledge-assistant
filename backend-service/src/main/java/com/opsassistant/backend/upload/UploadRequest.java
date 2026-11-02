package com.opsassistant.backend.upload;

/** An upload as the client asked for it, with its enums parsed but nothing checked against the database yet. */
public record UploadRequest(
        ContentKind contentKind,
        DocType docType,        // set only for documents
        TableType tableType,    // set only for data tables
        UploadMode mode,
        Long campaignId,        // nullable: an sop belongs to no campaign
        String title,           // sop only
        Scope scope,            // sop only
        Long replacesDocId,     // documents with mode=replace only
        String category         // nullable
) {
}
