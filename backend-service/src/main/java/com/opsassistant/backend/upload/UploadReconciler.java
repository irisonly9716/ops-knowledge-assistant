package com.opsassistant.backend.upload;

import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

/**
 * Compares each unfinished upload with Kafka, instead of assuming that every step succeeded. Age alone proves
 * nothing, because received includes time spent queueing: during an embedding API outage on one partition,
 * everything behind the first message looks stuck, and publishing all of it again would double the backlog at the
 * worst moment. So the decision is made on the consumer group's lag:
 *
 *   lag above 0: the message is still in Kafka and publishing it again can't help. If the group's position
 *                hasn't moved for a while, the consumer is dead or stuck: raise an alert, publish nothing.
 *   lag 0:       nothing is waiting and nothing is in flight (a message being processed isn't committed yet), so the
 *                message is gone, or it was dead-lettered and recording failed didn't succeed. Publish it again,
 *                which is safe because the consumer is idempotent, up to a limit (UploadService.republishIfStuck).
 *
 * If Kafka can't answer, nothing happens: without the lag nobody can say that a message is gone. The alert is an
 * error in the log, for whatever watches the logs where this is deployed.
 */
@Slf4j
@Service
public class UploadReconciler {

    /** What one run did. */
    public record Result(int republished, int gaveUp, List<String> stalledGroups) {
    }

    private record Position(long committed, Instant since) {
    }

    private final UploadRepository uploadRepository;
    private final UploadService uploadService;
    private final ConsumerLag consumerLag;
    private final Clock clock;
    private final Map<String, String> groupByContentKind;
    private final String topic;
    private final Duration stuckAfter;
    private final Duration stalledAfter;
    private final int maxRepublishes;
    private final int batchSize;

    private final Map<String, Position> lastPositions = new HashMap<>();

    @Autowired
    public UploadReconciler(UploadRepository uploadRepository, UploadService uploadService, ConsumerLag consumerLag,
                            @Value("${reconciliation.document-consumer-group}") String documentGroup,
                            @Value("${reconciliation.data-table-consumer-group}") String dataTableGroup,
                            @Value("${document.upload-topic}") String topic,
                            @Value("${reconciliation.stuck-after-ms}") long stuckAfterMs,
                            @Value("${reconciliation.stalled-after-ms}") long stalledAfterMs,
                            @Value("${reconciliation.max-republishes}") int maxRepublishes,
                            @Value("${reconciliation.batch-size}") int batchSize) {
        this(uploadRepository, uploadService, consumerLag, Clock.systemUTC(),
                Map.of(ContentKind.DOCUMENT.value(), documentGroup, ContentKind.DATA_TABLE.value(), dataTableGroup),
                topic, Duration.ofMillis(stuckAfterMs), Duration.ofMillis(stalledAfterMs), maxRepublishes, batchSize);
    }

    UploadReconciler(UploadRepository uploadRepository, UploadService uploadService, ConsumerLag consumerLag, Clock clock,
                     Map<String, String> groupByContentKind, String topic, Duration stuckAfter, Duration stalledAfter,
                     int maxRepublishes, int batchSize) {
        this.uploadRepository = uploadRepository;
        this.uploadService = uploadService;
        this.consumerLag = consumerLag;
        this.clock = clock;
        this.groupByContentKind = groupByContentKind;
        this.topic = topic;
        this.stuckAfter = stuckAfter;
        this.stalledAfter = stalledAfter;
        this.maxRepublishes = maxRepublishes;
        this.batchSize = batchSize;
    }

    /** Kafka is only asked when an upload looks stuck, and never while a database transaction is open. */
    public Result reconcile() {
        Instant now = clock.instant();
        Instant cutoff = now.minus(stuckAfter);
        List<Upload> stuck = uploadRepository.findStuck(cutoff, batchSize);
        if (stuck.isEmpty()) {
            lastPositions.clear();
            return new Result(0, 0, List.of());
        }

        // Each kind is read from the group that owns it, so a second consumer only needs a second setting
        Map<String, List<Upload>> byGroup = new LinkedHashMap<>();
        for (Upload upload : stuck) {
            byGroup.computeIfAbsent(groupByContentKind.get(upload.getContentKind()), group -> new ArrayList<>()).add(upload);
        }

        int republished = 0;
        int gaveUp = 0;
        List<String> stalled = new ArrayList<>();
        for (Map.Entry<String, List<Upload>> entry : byGroup.entrySet()) {
            String group = entry.getKey();
            ConsumerLag.Progress progress;
            try {
                progress = consumerLag.read(group, topic);
            } catch (RuntimeException e) {
                log.warn("reconciliation: leaving {} upload(s) alone, the lag of {} is unknown: {}", entry.getValue().size(), group, e.toString());
                continue;
            }
            if (progress.lag() > 0) {
                if (hasStalled(group, progress, now)) {
                    stalled.add(group);
                    log.error("reconciliation: consumer group {} has {} unfinished message(s) and hasn't moved for {} min, "
                                    + "so it is dead or stuck; {} upload(s) are waiting", group, progress.lag(),
                            stalledAfter.toMinutes(), entry.getValue().size());
                }
                continue;
            }
            lastPositions.remove(group);
            for (Upload upload : entry.getValue()) {
                switch (uploadService.republishIfStuck(upload.getUploadId(), cutoff, maxRepublishes)) {
                    case REPUBLISHED -> {
                        republished++;
                        log.warn("reconciliation: upload {} was {} with no message left in Kafka; published it again",
                                upload.getUploadId(), upload.getStatus());
                    }
                    case GAVE_UP -> {
                        gaveUp++;
                        log.error("reconciliation: upload {} still isn't finished after {} republishes; marked failed",
                                upload.getUploadId(), maxRepublishes);
                    }
                    case NOTHING_TO_DO -> {
                        // It moved on between the read and the lock
                    }
                }
            }
        }
        return new Result(republished, gaveUp, stalled);
    }

    /** True once the position hasn't changed for the limit, and again after each further period of that length. */
    private boolean hasStalled(String group, ConsumerLag.Progress progress, Instant now) {
        Position last = lastPositions.get(group);
        if (last == null || last.committed() != progress.committed()) {
            lastPositions.put(group, new Position(progress.committed(), now));
            return false;
        }
        if (Duration.between(last.since(), now).compareTo(stalledAfter) < 0) {
            return false;
        }
        lastPositions.put(group, new Position(progress.committed(), now));
        return true;
    }
}
