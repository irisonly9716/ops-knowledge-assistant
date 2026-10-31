package com.opsassistant.backend.outbox;

import java.time.Instant;
import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.transaction.annotation.Transactional;

public interface OutboxRepository extends JpaRepository<OutboxMessage, Long> {

    /**
     * The oldest unpublished messages, locked until the caller's transaction ends.
     * FOR UPDATE: no other transaction can publish or modify them meanwhile.
     * SKIP LOCKED: rows another relay instance has already locked are skipped instead of waited for, so
     * several backend instances can relay side by side without publishing the same row at once.
     */
    @Query(value = "SELECT * FROM outbox WHERE published_at IS NULL ORDER BY id LIMIT :limit FOR UPDATE SKIP LOCKED",
            nativeQuery = true)
    List<OutboxMessage> lockUnpublished(@Param("limit") int limit);

    /** Published messages are only kept for troubleshooting; delete them after the retention period. */
    @Transactional
    @Modifying
    @Query(value = "DELETE FROM outbox WHERE published_at < :before", nativeQuery = true)
    int deletePublishedBefore(@Param("before") Instant before);
}
