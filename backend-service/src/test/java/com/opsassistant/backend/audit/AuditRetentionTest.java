package com.opsassistant.backend.audit;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneOffset;
import org.junit.jupiter.api.Test;
import org.springframework.dao.DataAccessResourceFailureException;

/** Which rows the retention clears and how it walks a backlog. The SQL itself: QueryAuditIntegrationTest. */
class AuditRetentionTest {

    private static final Instant NOW = Instant.parse("2026-11-14T10:00:00Z");

    private final QueryAuditRepository audits = mock(QueryAuditRepository.class);
    private final AuditRetention retention = new AuditRetention(audits, Clock.fixed(NOW, ZoneOffset.UTC),
            Duration.ofDays(30), 1000);

    @Test
    void the_text_of_questions_and_comments_older_than_the_retention_is_cleared() {
        retention.clearOldText();

        verify(audits).clearTextBefore(Instant.parse("2026-10-15T10:00:00Z"), 1000);
        verify(audits).clearCommentsBefore(Instant.parse("2026-10-15T10:00:00Z"), 1000);
    }

    @Test
    void a_backlog_is_cleared_batch_after_batch_until_one_comes_back_short() {
        when(audits.clearTextBefore(any(), anyInt())).thenReturn(1000, 1000, 17);

        retention.clearOldText();

        verify(audits, times(3)).clearTextBefore(any(), anyInt());
        verify(audits, times(1)).clearCommentsBefore(any(), anyInt());
    }

    @Test
    void a_run_without_the_database_fails_quietly_and_the_next_one_tries_again() {
        when(audits.clearTextBefore(any(), anyInt())).thenThrow(new DataAccessResourceFailureException("down"));

        retention.clearOldText();   // nothing thrown at the scheduler

        verify(audits, times(0)).clearCommentsBefore(any(), anyInt());
    }
}
