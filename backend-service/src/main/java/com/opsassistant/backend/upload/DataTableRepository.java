package com.opsassistant.backend.upload;

import lombok.RequiredArgsConstructor;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

/** Reads the structured tables that data-table uploads fill (campaign_products, campaign_customer_behavior). */
@Repository
@RequiredArgsConstructor
public class DataTableRepository {

    private final JdbcTemplate jdbc;

    /**
     * Whether this campaign already has rows in the table: the "does it exist" of a data table.
     * Rows can exist without any upload (the mock data), so this looks at the table, not at uploads.
     * The table name is concatenated, which is safe only because it comes from the enum, never from a request.
     */
    public boolean hasRows(TableType table, long campaignId) {
        Boolean exists = jdbc.queryForObject(
                "SELECT EXISTS (SELECT 1 FROM " + table.value() + " WHERE campaign_id = ?)", Boolean.class, campaignId);
        return Boolean.TRUE.equals(exists);
    }
}
