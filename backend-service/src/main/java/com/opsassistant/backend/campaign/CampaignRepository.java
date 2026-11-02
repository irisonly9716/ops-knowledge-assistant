package com.opsassistant.backend.campaign;

import java.time.LocalDate;
import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface CampaignRepository extends JpaRepository<Campaign, Long> {

    /** A department's campaigns running at some point in the period: they start before it ends and end after it starts. */
    @Query("""
            SELECT c FROM Campaign c
            WHERE c.startDate <= :last AND c.endDate >= :first AND c.departmentId = :departmentId
            ORDER BY c.startDate, c.campaignId
            """)
    List<Campaign> findRunningBetween(@Param("first") LocalDate first, @Param("last") LocalDate last,
                                      @Param("departmentId") Long departmentId);

    /** The same across all departments. */
    @Query("""
            SELECT c FROM Campaign c
            WHERE c.startDate <= :last AND c.endDate >= :first
            ORDER BY c.startDate, c.campaignId
            """)
    List<Campaign> findRunningBetween(@Param("first") LocalDate first, @Param("last") LocalDate last);
}
