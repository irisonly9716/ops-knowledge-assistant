package com.opsassistant.backend.upload;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneId;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/** The decisions of the reconciliation job, with a fake lag and a hand-driven clock: no Kafka, no waiting. */
class UploadReconcilerTest {

    private static final String DOCUMENT_GROUP = "document-group";
    private static final String TABLE_GROUP = "table-group";
    private static final Duration STUCK_AFTER = Duration.ofMinutes(5);
    private static final Duration STALLED_AFTER = Duration.ofMinutes(10);

    private final UploadRepository uploads = mock(UploadRepository.class);
    private final UploadService service = mock(UploadService.class);
    private final FakeLag lag = new FakeLag();
    private final MutableClock clock = new MutableClock();
    private final UploadReconciler reconciler = new UploadReconciler(uploads, service, lag, clock,
            Map.of("document", DOCUMENT_GROUP, "data_table", TABLE_GROUP), "the-topic", STUCK_AFTER, STALLED_AFTER, 3, 100);

    @BeforeEach
    void setUp() {
        when(service.republishIfStuck(any(), any(), anyInt())).thenReturn(UploadService.Republish.REPUBLISHED);
    }

    private Upload stuck(String contentKind) {
        Upload upload = new Upload();
        upload.setUploadId(UUID.randomUUID());
        upload.setContentKind(contentKind);
        upload.changeStatus("received", null);
        return upload;
    }

    private void stuckUploads(Upload... found) {
        when(uploads.findStuck(any(), anyInt())).thenReturn(List.of(found));
    }

    @Test
    void with_nothing_stuck_kafka_is_not_even_asked() {
        stuckUploads();

        assertThat(reconciler.reconcile()).isEqualTo(new UploadReconciler.Result(0, 0, List.of()));

        assertThat(lag.reads).isEmpty();
    }

    @Test
    void it_looks_for_uploads_older_than_the_stuck_limit_within_a_batch() {
        stuckUploads();

        reconciler.reconcile();

        verify(uploads).findStuck(clock.instant().minus(STUCK_AFTER), 100);
    }

    // ------------------------------------------------------------------ lag above 0

    @Test
    void with_lag_above_zero_nothing_is_published_again_because_the_message_is_still_in_kafka() {
        stuckUploads(stuck("document"));
        lag.answer = new ConsumerLag.Progress(5, 100);

        assertThat(reconciler.reconcile().republished()).isZero();

        verify(service, never()).republishIfStuck(any(), any(), anyInt());
    }

    @Test
    void a_consumer_that_is_moving_raises_no_alert_however_long_the_backlog_lasts() {
        stuckUploads(stuck("document"));
        for (long position = 100; position < 110; position++) {
            lag.answer = new ConsumerLag.Progress(50, position);
            assertThat(reconciler.reconcile().stalledGroups()).isEmpty();
            clock.advance(STALLED_AFTER);
        }
    }

    @Test
    void a_position_that_stops_moving_raises_an_alert_only_after_the_limit_and_still_publishes_nothing() {
        stuckUploads(stuck("document"));
        lag.answer = new ConsumerLag.Progress(5, 100);

        assertThat(reconciler.reconcile().stalledGroups()).isEmpty();          // the first look
        clock.advance(STALLED_AFTER.minusSeconds(1));
        assertThat(reconciler.reconcile().stalledGroups()).isEmpty();          // not yet
        clock.advance(Duration.ofSeconds(1));
        assertThat(reconciler.reconcile().stalledGroups()).containsExactly(DOCUMENT_GROUP);

        verify(service, never()).republishIfStuck(any(), any(), anyInt());
    }

    @Test
    void an_alert_repeats_once_per_period_not_on_every_run() {
        stuckUploads(stuck("document"));
        lag.answer = new ConsumerLag.Progress(5, 100);
        reconciler.reconcile();
        clock.advance(STALLED_AFTER);
        assertThat(reconciler.reconcile().stalledGroups()).hasSize(1);

        clock.advance(Duration.ofMinutes(1));
        assertThat(reconciler.reconcile().stalledGroups()).isEmpty();
        clock.advance(STALLED_AFTER);
        assertThat(reconciler.reconcile().stalledGroups()).hasSize(1);
    }

    @Test
    void a_position_that_moves_starts_the_wait_for_an_alert_over() {
        stuckUploads(stuck("document"));
        lag.answer = new ConsumerLag.Progress(5, 100);
        reconciler.reconcile();
        clock.advance(STALLED_AFTER.minusSeconds(1));
        lag.answer = new ConsumerLag.Progress(4, 101);
        reconciler.reconcile();
        clock.advance(STALLED_AFTER.minusSeconds(1));

        assertThat(reconciler.reconcile().stalledGroups()).isEmpty();
    }

    @Test
    void when_nothing_is_stuck_anymore_the_wait_starts_over() {
        Upload upload = stuck("document");
        stuckUploads(upload);
        lag.answer = new ConsumerLag.Progress(5, 100);
        reconciler.reconcile();

        stuckUploads();
        clock.advance(STALLED_AFTER);
        reconciler.reconcile();

        stuckUploads(upload);
        assertThat(reconciler.reconcile().stalledGroups()).isEmpty();
    }

    // ------------------------------------------------------------------ lag 0

    @Test
    void with_lag_zero_each_stuck_upload_is_published_again_with_the_limits() {
        Upload first = stuck("document");
        Upload second = stuck("document");
        stuckUploads(first, second);
        lag.answer = new ConsumerLag.Progress(0, 100);

        assertThat(reconciler.reconcile()).isEqualTo(new UploadReconciler.Result(2, 0, List.of()));

        Instant cutoff = clock.instant().minus(STUCK_AFTER);
        verify(service).republishIfStuck(first.getUploadId(), cutoff, 3);
        verify(service).republishIfStuck(second.getUploadId(), cutoff, 3);
    }

    @Test
    void what_the_service_decides_is_what_the_run_reports() {
        Upload gaveUp = stuck("document");
        Upload movedOn = stuck("document");
        stuckUploads(gaveUp, movedOn);
        lag.answer = new ConsumerLag.Progress(0, 100);
        when(service.republishIfStuck(eq(gaveUp.getUploadId()), any(), anyInt())).thenReturn(UploadService.Republish.GAVE_UP);
        when(service.republishIfStuck(eq(movedOn.getUploadId()), any(), anyInt())).thenReturn(UploadService.Republish.NOTHING_TO_DO);

        assertThat(reconciler.reconcile()).isEqualTo(new UploadReconciler.Result(0, 1, List.of()));
    }

    // ------------------------------------------------------------------ groups

    @Test
    void each_upload_is_judged_by_the_group_that_owns_its_kind_on_the_right_topic() {
        Upload document = stuck("document");
        Upload table = stuck("data_table");
        stuckUploads(document, table);
        lag.answers = Map.of(DOCUMENT_GROUP, new ConsumerLag.Progress(7, 100), TABLE_GROUP, new ConsumerLag.Progress(0, 100));

        assertThat(reconciler.reconcile().republished()).isEqualTo(1);

        assertThat(lag.reads).containsExactlyInAnyOrder(DOCUMENT_GROUP + "@the-topic", TABLE_GROUP + "@the-topic");
        verify(service, never()).republishIfStuck(eq(document.getUploadId()), any(), anyInt());
        verify(service).republishIfStuck(eq(table.getUploadId()), any(), anyInt());
    }

    @Test
    void the_lag_is_read_once_per_group_however_many_uploads_are_stuck() {
        stuckUploads(stuck("document"), stuck("document"), stuck("document"));
        lag.answer = new ConsumerLag.Progress(0, 100);

        reconciler.reconcile();

        assertThat(lag.reads).hasSize(1);
    }

    // ------------------------------------------------------------------ when kafka can't answer

    @Test
    void if_the_lag_is_unknown_nothing_is_published_again() {
        stuckUploads(stuck("document"));
        lag.failure = new IllegalStateException("Kafka is down");

        assertThat(reconciler.reconcile()).isEqualTo(new UploadReconciler.Result(0, 0, List.of()));

        verify(service, never()).republishIfStuck(any(), any(), anyInt());
    }

    @Test
    void one_group_being_unknown_does_not_stop_the_other() {
        Upload document = stuck("document");
        Upload table = stuck("data_table");
        stuckUploads(document, table);
        lag.failureFor = DOCUMENT_GROUP;
        lag.answer = new ConsumerLag.Progress(0, 100);

        assertThat(reconciler.reconcile().republished()).isEqualTo(1);

        verify(service).republishIfStuck(eq(table.getUploadId()), any(), anyInt());
    }

    // ------------------------------------------------------------------ fakes

    private static final class FakeLag implements ConsumerLag {
        Progress answer;
        Map<String, Progress> answers;
        RuntimeException failure;
        String failureFor;
        final List<String> reads = new ArrayList<>();

        @Override
        public Progress read(String consumerGroup, String topic) {
            reads.add(consumerGroup + "@" + topic);
            if (failure != null) {
                throw failure;
            }
            if (consumerGroup.equals(failureFor)) {
                throw new IllegalStateException("Kafka is down for this group");
            }
            return answers != null ? answers.get(consumerGroup) : answer;
        }
    }

    private static final class MutableClock extends Clock {
        private Instant now = Instant.parse("2026-11-03T12:00:00Z");

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
