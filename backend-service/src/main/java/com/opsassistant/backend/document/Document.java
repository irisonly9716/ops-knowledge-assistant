package com.opsassistant.backend.document;

import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.util.UUID;
import lombok.AllArgsConstructor;
import lombok.Getter;
import lombok.NoArgsConstructor;
import org.hibernate.annotations.Immutable;

/**
 * The documents table, read-only here: the llm-service consumer writes it. The backend reads it to check
 * uploads against what already exists (natural keys, the version being replaced). Maps only those columns.
 */
@Entity
@Table(name = "documents")
@Immutable
@Getter
@NoArgsConstructor
@AllArgsConstructor
public class Document {

    @Id
    private Long docId;

    private String docType;
    private String title;
    private String department;   // department name, or ALL
    private Long campaignId;
    private UUID uploadId;       // the upload that produced the current version
    private int version;
}
