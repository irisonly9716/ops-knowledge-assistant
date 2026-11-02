package com.opsassistant.backend.campaign;

import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.LocalDate;
import lombok.AllArgsConstructor;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

/**
 * The campaigns table created in db/migration/. Campaigns are not created through backend-service
 * here (the mock data script creates them; in reality another system would sync them in), so there
 * is no @GeneratedValue: this entity is only read, to check an upload's campaign and to list campaigns.
 * It maps only the columns it needs, not every column of campaigns (Hibernate's validate mode checks that
 * the mapped fields match the table; it doesn't require every column to be mapped).
 */
@Entity
@Table(name = "campaigns")
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
public class Campaign {

    @Id
    private Long campaignId;

    private String name;
    private Long departmentId;
    private LocalDate startDate;
    private LocalDate endDate;
    private String status;
    private boolean hasPlanDoc;
    private boolean hasReviewDoc;
}
