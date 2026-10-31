package com.opsassistant.backend.outbox;

import java.util.List;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

/**
 * Publishes outbox rows to Kafka (the "relay" half of the transactional outbox).
 *
 * One run, in one transaction: lock the oldest unpublished rows, publish them in order, wait for Kafka's
 * acknowledgement of each, mark it published. The row locks are held while sending, which is what keeps a
 * second backend instance from publishing the same rows at the same time; each send is bounded by the
 * producer timeouts and a batch stops at the first failure, so the transaction stays short.
 *
 * Any failure -- Kafka refused the message, or no acknowledgement arrived in time -- is recorded on the row
 * and the row is retried on a later run. "No acknowledgement in time" is ambiguous: the message may have
 * been written anyway. Sending it again is safe because the consumer is idempotent (an upload that is
 * already done is skipped), which is exactly why the outbox gives at-least-once, not exactly-once, delivery.
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class OutboxRelay {

    private final OutboxRepository outboxRepository;
    private final KafkaTemplate<String, String> kafkaTemplate;

    @Value("${outbox.batch-size}")
    private int batchSize;

    @Value("${outbox.send-timeout-ms}")
    private long sendTimeoutMs;

    /** Publishes the oldest unpublished messages. Returns how many were published, or -1 if a send failed. */
    @Transactional
    public int publishBatch() {
        List<OutboxMessage> batch = outboxRepository.lockUnpublished(batchSize);
        int published = 0;
        for (OutboxMessage message : batch) {
            try {
                kafkaTemplate.send(message.getTopic(), message.getMessageKey(), message.getPayload())
                        .get(sendTimeoutMs, TimeUnit.MILLISECONDS);
                message.markPublished();   // a managed entity: the change is written when the transaction commits
                published++;
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();   // keep the "asked to stop" signal for whoever runs us
                message.recordFailure("interrupted while waiting for Kafka");
                return -1;
            } catch (ExecutionException | TimeoutException | RuntimeException e) {
                // ExecutionException: Kafka refused it. TimeoutException (java.util.concurrent): no answer in
                // time. RuntimeException: send() itself threw, typically org.apache.kafka.common.errors.
                // TimeoutException when the topic is missing or the broker unreachable -- a different class
                // with the same simple name, which is why RuntimeException is caught too.
                // Stop at the first failure: Kafka is most likely down, and the messages behind this one
                // must not overtake it
                Throwable cause = e instanceof ExecutionException && e.getCause() != null ? e.getCause() : e;
                message.recordFailure(cause.getClass().getSimpleName() + ": " + cause.getMessage());
                log.warn("outbox id={} key={} not published (attempt {}): {}", message.getId(), message.getMessageKey(),
                        message.getAttempts(), message.getLastError());
                return -1;
            }
        }
        if (published > 0) {
            log.info("outbox: published {} message(s)", published);
        }
        return published;
    }
}
