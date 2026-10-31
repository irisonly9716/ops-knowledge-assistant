package com.opsassistant.backend.outbox;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.util.List;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;
import org.apache.kafka.clients.producer.RecordMetadata;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.support.SendResult;
import org.springframework.test.util.ReflectionTestUtils;

/**
 * Unit tests for the relay: Kafka is a Mockito fake, so every failure can be produced on demand. The
 * Kafka failure modes here are the ones that used to be handled in the upload request (each was a real
 * bug once); now they only delay publishing, and the message stays in the outbox until it goes through.
 * The row locking against a real Postgres is tested in OutboxIntegrationTest.
 */
class OutboxRelayTest {

    private final OutboxRepository repository = mock(OutboxRepository.class);
    @SuppressWarnings("unchecked")
    private final KafkaTemplate<String, String> kafka = mock(KafkaTemplate.class);
    private final OutboxRelay relay = new OutboxRelay(repository, kafka);

    private final OutboxMessage first = new OutboxMessage("topic", "upload-1", "{\"n\":1}");
    private final OutboxMessage second = new OutboxMessage("topic", "upload-2", "{\"n\":2}");

    @BeforeEach
    void setUp() {
        ReflectionTestUtils.setField(relay, "batchSize", 50);
        ReflectionTestUtils.setField(relay, "sendTimeoutMs", 300L);   // 15 s in production
        when(repository.lockUnpublished(50)).thenReturn(List.of(first, second));
    }

    @Test
    void publishes_in_order_and_marks_each_message_published() {
        CompletableFuture<SendResult<String, String>> ok = succeeded();
        when(kafka.send(anyString(), anyString(), anyString())).thenReturn(ok);

        assertThat(relay.publishBatch()).isEqualTo(2);

        var order = org.mockito.Mockito.inOrder(kafka);
        order.verify(kafka).send("topic", "upload-1", "{\"n\":1}");   // the stored payload, byte for byte
        order.verify(kafka).send("topic", "upload-2", "{\"n\":2}");
        assertThat(first.getPublishedAt()).isNotNull();
        assertThat(second.getPublishedAt()).isNotNull();
    }

    @Test
    void nothing_to_publish_returns_zero() {
        when(repository.lockUnpublished(50)).thenReturn(List.of());
        assertThat(relay.publishBatch()).isZero();
    }

    /**
     * Topic missing / broker unreachable: send() ITSELF throws org.apache.kafka.common.errors.TimeoutException
     * (not a failed Future). Same simple name as java.util.concurrent.TimeoutException, different class.
     */
    @Test
    void send_throwing_kafkas_own_TimeoutException_is_recorded_and_stops_the_batch() {
        when(kafka.send(anyString(), anyString(), anyString()))
                .thenThrow(new org.apache.kafka.common.errors.TimeoutException("Topic topic not present in metadata"));

        assertThat(relay.publishBatch()).isEqualTo(-1);

        assertThat(first.getPublishedAt()).isNull();
        assertThat(first.getAttempts()).isEqualTo(1);
        assertThat(first.getLastError()).contains("TimeoutException").contains("not present in metadata");
        // the second message is not tried: it must not overtake the first
        verify(kafka, never()).send(eq("topic"), eq("upload-2"), anyString());
        assertThat(second.getAttempts()).isZero();
    }

    @Test
    void a_failed_future_is_recorded_with_its_cause() {
        when(kafka.send(anyString(), anyString(), anyString()))
                .thenReturn(CompletableFuture.failedFuture(new RuntimeException("broker down")));

        assertThat(relay.publishBatch()).isEqualTo(-1);

        assertThat(first.getPublishedAt()).isNull();
        assertThat(first.getLastError()).isEqualTo("RuntimeException: broker down");
    }

    /**
     * No acknowledgement in time: the message may have been written anyway. It is retried, so it may be
     * published twice; the consumer is idempotent, which is what makes resending safe.
     */
    @Test
    void no_acknowledgement_in_time_is_retried_later() {
        when(kafka.send(anyString(), anyString(), anyString())).thenReturn(new CompletableFuture<>()); // never completes

        assertThat(relay.publishBatch()).isEqualTo(-1);

        assertThat(first.getPublishedAt()).isNull();   // still unpublished -> picked up again on the next run
        assertThat(first.getLastError()).startsWith("TimeoutException");
    }

    @Test
    @SuppressWarnings("unchecked")
    void interruption_stops_the_run_and_restores_the_interrupt_flag() throws Exception {
        CompletableFuture<SendResult<String, String>> future = mock(CompletableFuture.class);
        when(future.get(anyLong(), eq(TimeUnit.MILLISECONDS))).thenThrow(new InterruptedException("test"));
        when(kafka.send(anyString(), anyString(), anyString())).thenReturn(future);

        try {
            assertThat(relay.publishBatch()).isEqualTo(-1);
            assertThat(first.getPublishedAt()).isNull();
            assertThat(Thread.currentThread().isInterrupted())
                    .as("after catching InterruptedException, Thread.currentThread().interrupt() must restore the flag")
                    .isTrue();
        } finally {
            Thread.interrupted(); // clear the flag so later tests are unaffected
        }
    }

    @Test
    void a_message_that_failed_before_goes_through_and_its_error_is_cleared() {
        first.recordFailure("broker down");
        CompletableFuture<SendResult<String, String>> ok = succeeded();
        when(kafka.send(anyString(), anyString(), anyString())).thenReturn(ok);

        relay.publishBatch();

        assertThat(first.getPublishedAt()).isNotNull();
        assertThat(first.getLastError()).isNull();
        assertThat(first.getAttempts()).isEqualTo(1);   // the history of failed attempts stays
    }

    /**
     * Build the future completely before stubbing kafka.send: stubbing other mocks inside a
     * when(...).thenReturn(...) is an UnfinishedStubbingException.
     */
    @SuppressWarnings("unchecked")
    private static CompletableFuture<SendResult<String, String>> succeeded() {
        RecordMetadata metadata = mock(RecordMetadata.class);
        SendResult<String, String> result = mock(SendResult.class);
        when(result.getRecordMetadata()).thenReturn(metadata);
        return CompletableFuture.completedFuture(result);
    }
}
