package com.opsassistant.backend.document;

import java.util.Optional;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface DocumentRepository extends JpaRepository<Document, Long> {

    /**
     * The SOP with this natural key: same department (or ALL), same title after normalizing. The comparison
     * is the expression of the unique index uq_documents_sop_title (V3), so it finds exactly what the index
     * would reject.
     */
    @Query(value = """
            SELECT * FROM documents
            WHERE doc_type = 'sop' AND department = :department
              AND lower(btrim(normalize(title, NFKC))) = lower(btrim(normalize(CAST(:title AS text), NFKC)))
            """, nativeQuery = true)
    Optional<Document> findSop(@Param("department") String department, @Param("title") String title);

    /** The plan or review of a campaign: its natural key. */
    Optional<Document> findByCampaignIdAndDocType(Long campaignId, String docType);

    /** Whether this upload still produced the current version of some document. */
    boolean existsByUploadId(UUID uploadId);
}
