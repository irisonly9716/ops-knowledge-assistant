package com.opsassistant.backend.outbox;

import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.annotation.EnableScheduling;
import org.springframework.scheduling.annotation.Scheduled;

/**
 * Runs the relay every poll interval, and backs off while publishing keeps failing: with Kafka down, every
 * run would otherwise block on the producer timeout and log the same warning once a second. Also deletes
 * published messages after the retention period.
 *
 * Switched off with outbox.relay.enabled=false (tests call the relay directly instead).
 */
@Slf4j
@Configuration(proxyBeanMethods = false)
@EnableScheduling
@ConditionalOnProperty(name = "outbox.relay.enabled", havingValue = "true", matchIfMissing = true)
public class OutboxScheduler {

    private final OutboxRelay relay;
    private final OutboxRepository repository;
    private final Clock clock;
    private final Duration pollInterval;
    private final Duration maxBackoff;
    private final Duration retention;

    private int consecutiveFailures;
    private Instant nextAttempt = Instant.EPOCH;

    @Autowired
    public OutboxScheduler(OutboxRelay relay, OutboxRepository repository,
                           @Value("${outbox.poll-interval-ms}") long pollIntervalMs,
                           @Value("${outbox.max-backoff-ms}") long maxBackoffMs,
                           @Value("${outbox.retention-days}") long retentionDays) {
        this(relay, repository, Clock.systemUTC(), Duration.ofMillis(pollIntervalMs), Duration.ofMillis(maxBackoffMs),
                Duration.ofDays(retentionDays));
    }

    OutboxScheduler(OutboxRelay relay, OutboxRepository repository, Clock clock, Duration pollInterval,
                    Duration maxBackoff, Duration retention) {
        this.relay = relay;
        this.repository = repository;
        this.clock = clock;
        this.pollInterval = pollInterval;
        this.maxBackoff = maxBackoff;
        this.retention = retention;
    }

    @Scheduled(fixedDelayString = "${outbox.poll-interval-ms}")
    public void relay() {
        if (clock.instant().isBefore(nextAttempt)) {
            return;   // backing off after failures
        }
        try {
            int published;
            do {
                published = relay.publishBatch();   // keep going while there is a backlog
            } while (published > 0);
            if (published == 0) {
                if (consecutiveFailures > 0) {
                    log.info("outbox: publishing works again after {} failed run(s)", consecutiveFailures);
                }
                consecutiveFailures = 0;
                return;
            }
        } catch (RuntimeException e) {   // the database itself is unreachable, for example
            log.warn("outbox: relay run failed: {}", e.toString());
        }
        consecutiveFailures++;
        // poll interval x 2, x 4, x 8 ... up to the maximum
        Duration delay = pollInterval.multipliedBy(1L << Math.min(consecutiveFailures, 20));
        delay = delay.compareTo(maxBackoff) > 0 ? maxBackoff : delay;
        nextAttempt = clock.instant().plus(delay);
    }

    @Scheduled(fixedDelayString = "${outbox.cleanup-interval-ms}")
    public void deleteOldPublished() {
        int deleted = repository.deletePublishedBefore(clock.instant().minus(retention));
        if (deleted > 0) {
            log.info("outbox: deleted {} published message(s) older than {} days", deleted, retention.toDays());
        }
    }
}
