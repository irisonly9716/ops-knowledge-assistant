package com.opsassistant.backend.audit;

import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.function.IntSupplier;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.annotation.EnableScheduling;
import org.springframework.scheduling.annotation.Scheduled;

/**
 * Clears what people wrote once the retention period is over: the question, its rewrite and its SQL in query_audit,
 * and feedback comments. The numbers stay. Long enough to look into a complaint or a thumbs down, short enough that
 * what someone asked isn't kept for good.
 *
 * In batches, each its own short transaction, so a backlog never holds many row locks at once. The first run waits one
 * interval. Switched off with audit.retention.enabled=false, as in every test: they call it directly.
 */
@Slf4j
@Configuration(proxyBeanMethods = false)
@EnableScheduling
@ConditionalOnProperty(name = "audit.retention.enabled", havingValue = "true", matchIfMissing = true)
public class AuditRetention {

    private final QueryAuditRepository audits;
    private final Clock clock;
    private final Duration retention;
    private final int batchSize;

    @Autowired
    public AuditRetention(QueryAuditRepository audits, @Value("${audit.retention.days}") long retentionDays,
                          @Value("${audit.retention.batch-size}") int batchSize) {
        this(audits, Clock.systemUTC(), Duration.ofDays(retentionDays), batchSize);
    }

    AuditRetention(QueryAuditRepository audits, Clock clock, Duration retention, int batchSize) {
        this.audits = audits;
        this.clock = clock;
        this.retention = retention;
        this.batchSize = batchSize;
    }

    @Scheduled(initialDelayString = "${audit.retention.interval-ms}", fixedDelayString = "${audit.retention.interval-ms}")
    public void clearOldText() {
        Instant cutoff = clock.instant().minus(retention);
        try {
            int questions = drained(() -> audits.clearTextBefore(cutoff, batchSize));
            int comments = drained(() -> audits.clearCommentsBefore(cutoff, batchSize));
            if (questions + comments > 0) {
                log.info("audit retention: cleared the text of {} question(s) and {} comment(s) older than {} days",
                        questions, comments, retention.toDays());
            }
        } catch (RuntimeException e) {   // the database is unreachable, for example: the next run tries again
            log.warn("audit retention: run failed: {}", e.toString());
        }
    }

    /** Batch after batch until one comes back short. */
    private int drained(IntSupplier batch) {
        int total = 0;
        int cleared;
        do {
            cleared = batch.getAsInt();
            total += cleared;
        } while (cleared == batchSize);
        return total;
    }
}
