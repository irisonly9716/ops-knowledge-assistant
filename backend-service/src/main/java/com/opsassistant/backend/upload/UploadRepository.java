package com.opsassistant.backend.upload;

import jakarta.persistence.LockModeType;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface UploadRepository extends JpaRepository<Upload, UUID> {

    /** The row locked until the transaction ends: two reviewers acting at once are handled one after the other. */
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("SELECT u FROM Upload u WHERE u.uploadId = :uploadId")
    Optional<Upload> lockById(@Param("uploadId") UUID uploadId);

    /** Waiting uploads of the staff of one department: what that department's manager has to decide. */
    @Query("""
            SELECT u FROM Upload u, User uploader
            WHERE u.uploadedBy = uploader.userId AND u.status = 'pending_approval'
              AND uploader.role = 'staff' AND uploader.departmentId = :departmentId
            ORDER BY u.createdAt
            """)
    List<Upload> findPendingFromStaffOf(@Param("departmentId") Long departmentId);

    /** Waiting uploads of all managers: what the executive has to decide. */
    @Query("""
            SELECT u FROM Upload u, User uploader
            WHERE u.uploadedBy = uploader.userId AND u.status = 'pending_approval' AND uploader.role = 'manager'
            ORDER BY u.createdAt
            """)
    List<Upload> findPendingFromManagers();

    /**
     * The upload that produced the data currently in a table for a campaign: the last one that finished.
     * A data table has no documents row to point at its current version, so this is its "current version".
     */
    Optional<Upload> findFirstByCampaignIdAndTableTypeAndStatusOrderByUpdatedAtDesc(Long campaignId, String tableType,
                                                                                    String status);
}
