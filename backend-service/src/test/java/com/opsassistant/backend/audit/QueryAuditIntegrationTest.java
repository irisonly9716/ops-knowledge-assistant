package com.opsassistant.backend.audit;

import static org.assertj.core.api.Assertions.assertThat;

import java.time.Instant;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Assumptions;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;

/**
 * QueryAuditRepository's SQL against the real Postgres (docker compose), migration V13 applied. It runs against the
 * development database, so it only touches audit rows it creates: dated in 2001, which no real question is, and
 * deleted afterwards with their feedback.
 * Run with: .\mvnw.cmd test -DexcludedGroups=none -Dgroups=integration
 */
@Tag("integration")
@SpringBootTest(properties = "outbox.relay.enabled=false")
class QueryAuditIntegrationTest {

    private static final Instant CUTOFF = Instant.parse("2001-06-01T00:00:00Z");

    @Autowired
    QueryAuditRepository audits;
    @Autowired
    JdbcTemplate jdbc;

    private final UUID old = UUID.randomUUID();
    private final UUID recent = UUID.randomUUID();
    private final UUID executives = UUID.randomUUID();
    private long staff;
    private long executive;

    @BeforeEach
    void rows() {
        List<Long> staffIds = jdbc.queryForList("SELECT user_id FROM users WHERE role = 'staff' ORDER BY user_id LIMIT 1",
                Long.class);
        List<Long> executiveIds = jdbc.queryForList(
                "SELECT user_id FROM users WHERE role = 'executive' ORDER BY user_id LIMIT 1", Long.class);
        Assumptions.assumeTrue(!staffIds.isEmpty() && !executiveIds.isEmpty(), "needs the mock users");
        staff = staffIds.getFirst();
        executive = executiveIds.getFirst();
        insert(old, staff, "staff", "Beauty", "2001-01-10T09:00:00Z");
        insert(recent, staff, "staff", "Beauty", "2001-12-10T09:00:00Z");
        insert(executives, executive, "executive", null, "2001-12-11T09:00:00Z");
    }

    private void insert(UUID id, long user, String role, String department, String at) {
        jdbc.update("""
                INSERT INTO query_audit (query_id, created_at, user_id, role, department, outcome, route, paths, question,
                                         rewritten, sql, cited_doc_ids, model_calls, input_tokens, output_tokens, tokens,
                                         cost_usd, total_ms, timings_ms)
                VALUES (?, ?::timestamptz, ?, ?, ?, 'answered', 'data', '{data}', 'What did we make?', 'What did Beauty make?',
                        'SELECT 1', '{12,40}', 3, 2100, 160, '{"router": [300, 25]}', 0.00000015, 2412, '{"route": 1210}')
                """, id, at, user, role, department);
    }

    @AfterEach
    void cleanUp() {
        jdbc.update("DELETE FROM query_audit WHERE query_id IN (?, ?, ?)", old, recent, executives);   // feedback cascades
    }

    @Test
    void the_asker_of_a_question_is_found_by_its_id() {
        assertThat(audits.askerOf(recent)).contains(staff);
        assertThat(audits.askerOf(UUID.randomUUID())).isEmpty();
    }

    @Test
    void a_second_rating_replaces_the_first_comment_included() {
        audits.saveFeedback(recent, "down", "Wrong campaign.");
        audits.saveFeedback(recent, "up", null);

        assertThat(jdbc.queryForList("SELECT rating, comment FROM query_feedback WHERE query_id = ?", recent))
                .singleElement()
                .satisfies(row -> {
                    assertThat(row.get("rating")).isEqualTo("up");
                    assertThat(row.get("comment")).isNull();
                });
    }

    @Test
    void the_listing_is_newest_first_with_the_feedback_and_the_stored_json_and_arrays_as_they_are() {
        audits.saveFeedback(recent, "down", "Wrong campaign.");

        List<AuditEntry> page = audits.list(new QueryAuditRepository.Filter(staff, null,
                OffsetDateTime.parse("2002-01-01T00:00:00Z"), 200));

        assertThat(page).extracting(AuditEntry::queryId).containsSubsequence(recent, old);
        AuditEntry entry = page.stream().filter(e -> e.queryId().equals(recent)).findFirst().orElseThrow();
        assertThat(entry.paths()).containsExactly("data");
        assertThat(entry.citedDocIds()).containsExactly(12L, 40L);
        assertThat(entry.tokens()).isEqualTo("{\"router\": [300, 25]}");
        assertThat(entry.timingsMs()).isEqualTo("{\"route\": 1210}");
        assertThat(entry.costUsd()).isEqualTo("0.00000015");
        assertThat(entry.crossDepartment()).isFalse();
        assertThat(entry.rating()).isEqualTo("down");
        assertThat(entry.comment()).isEqualTo("Wrong campaign.");
    }

    @Test
    void the_cross_department_filter_keeps_only_executives_and_admins_questions() {
        OffsetDateTime before = OffsetDateTime.parse("2002-01-01T00:00:00Z");

        assertThat(audits.list(new QueryAuditRepository.Filter(null, true, before, 200)))
                .extracting(AuditEntry::queryId).contains(executives).doesNotContain(old, recent);
        assertThat(audits.list(new QueryAuditRepository.Filter(null, false, before, 200)))
                .extracting(AuditEntry::queryId).contains(old, recent).doesNotContain(executives);
    }

    @Test
    void the_next_page_starts_below_the_last_row_of_the_one_before() {
        OffsetDateTime lastShown = OffsetDateTime.parse("2001-12-10T09:00:00Z");

        assertThat(audits.list(new QueryAuditRepository.Filter(staff, null, lastShown, 200)))
                .extracting(AuditEntry::queryId).contains(old).doesNotContain(recent, executives);
    }

    @Test
    void retention_clears_the_text_of_old_questions_and_comments_and_keeps_the_numbers() {
        audits.saveFeedback(old, "down", "Wrong campaign.");
        audits.saveFeedback(recent, "down", "Too slow.");
        jdbc.update("UPDATE query_feedback SET updated_at = '2001-01-10T09:00:00Z' WHERE query_id = ?", old);

        assertThat(audits.clearTextBefore(CUTOFF, 1000)).isEqualTo(1);
        assertThat(audits.clearCommentsBefore(CUTOFF, 1000)).isEqualTo(1);
        assertThat(audits.clearTextBefore(CUTOFF, 1000)).isZero();   // already cleared: not found again

        var cleared = jdbc.queryForMap("SELECT question, rewritten, sql, total_ms, cost_usd, input_tokens FROM query_audit "
                + "WHERE query_id = ?", old);
        assertThat(cleared.get("question")).isNull();
        assertThat(cleared.get("rewritten")).isNull();
        assertThat(cleared.get("sql")).isNull();
        assertThat(cleared.get("total_ms")).isEqualTo(2412);
        assertThat(cleared.get("input_tokens")).isEqualTo(2100);
        assertThat(jdbc.queryForObject("SELECT question FROM query_audit WHERE query_id = ?", String.class, recent))
                .isEqualTo("What did we make?");
        assertThat(jdbc.queryForObject("SELECT comment FROM query_feedback WHERE query_id = ?", String.class, old)).isNull();
        assertThat(jdbc.queryForObject("SELECT comment FROM query_feedback WHERE query_id = ?", String.class, recent))
                .isEqualTo("Too slow.");
    }
}
