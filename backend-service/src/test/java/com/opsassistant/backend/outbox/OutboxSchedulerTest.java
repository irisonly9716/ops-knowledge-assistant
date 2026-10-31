package com.opsassistant.backend.outbox;

import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneId;
import java.time.ZoneOffset;
import org.junit.jupiter.api.Test;

/** The scheduler's loop and backoff, with a hand-driven clock: no waiting, no Spring. */
class OutboxSchedulerTest {

    private final OutboxRelay relay = mock(OutboxRelay.class);
    private final OutboxRepository repository = mock(OutboxRepository.class);
    private final MutableClock clock = new MutableClock();
    private final OutboxScheduler scheduler = new OutboxScheduler(relay, repository, clock,
            Duration.ofSeconds(1), Duration.ofSeconds(30), Duration.ofDays(7));

    @Test
    void a_backlog_is_drained_in_one_run() {
        when(relay.publishBatch()).thenReturn(50, 50, 7, 0);

        scheduler.relay();

        verify(relay, times(4)).publishBatch();
    }

    @Test
    void after_failures_it_backs_off_exponentially_up_to_the_maximum() {
        when(relay.publishBatch()).thenReturn(-1);

        scheduler.relay();                       // failure 1 -> next attempt in 2 s
        clock.advance(Duration.ofSeconds(1));
        scheduler.relay();                       // too early: skipped
        verify(relay, times(1)).publishBatch();

        clock.advance(Duration.ofSeconds(1));
        scheduler.relay();                       // 2 s later: tried, failure 2 -> next attempt in 4 s
        verify(relay, times(2)).publishBatch();

        clock.advance(Duration.ofSeconds(3));
        scheduler.relay();                       // only 3 s: skipped
        verify(relay, times(2)).publishBatch();

        for (int i = 0; i < 10; i++) {           // many more failures: the wait is capped at 30 s
            clock.advance(Duration.ofSeconds(30));
            scheduler.relay();
        }
        verify(relay, times(12)).publishBatch();
    }

    @Test
    void a_successful_run_resets_the_backoff() {
        when(relay.publishBatch()).thenReturn(-1, -1, 0, -1);

        scheduler.relay();                       // failure 1 (wait 2 s)
        clock.advance(Duration.ofSeconds(2));
        scheduler.relay();                       // failure 2 (wait 4 s)
        clock.advance(Duration.ofSeconds(4));
        scheduler.relay();                       // success
        scheduler.relay();                       // right away again: no backoff left -> failure 1 again
        clock.advance(Duration.ofSeconds(2));
        scheduler.relay();                       // so 2 s later it is tried again, not 8 s
        verify(relay, times(5)).publishBatch();
    }

    @Test
    void a_database_error_also_backs_off_instead_of_retrying_every_second() {
        when(relay.publishBatch()).thenThrow(new RuntimeException("connection refused"));

        scheduler.relay();
        clock.advance(Duration.ofSeconds(1));
        scheduler.relay();

        verify(relay, times(1)).publishBatch();
    }

    @Test
    void published_messages_older_than_the_retention_are_deleted() {
        scheduler.deleteOldPublished();
        verify(repository).deletePublishedBefore(clock.instant().minus(Duration.ofDays(7)));
    }

    private static final class MutableClock extends Clock {
        private Instant now = Instant.parse("2026-10-31T12:00:00Z");

        void advance(Duration duration) {
            now = now.plus(duration);
        }

        @Override
        public Instant instant() {
            return now;
        }

        @Override
        public ZoneId getZone() {
            return ZoneOffset.UTC;
        }

        @Override
        public Clock withZone(ZoneId zone) {
            return this;
        }
    }
}
