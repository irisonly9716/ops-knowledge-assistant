package com.opsassistant.backend.audit;

import com.fasterxml.jackson.annotation.JsonRawValue;
import java.time.Instant;
import java.util.List;
import java.util.UUID;

/**
 * One question as the admin reads it: its audit row (migration V13) with the asker's feedback, if any. question,
 * rewritten, sql and comment are null once the retention period is over. tokens and timingsMs are passed on as the
 * JSON they are stored as; costUsd is text, so the amount stays exact. modelCalls, the tokens and costUsd are null when
 * nothing came back from the question, so what it spent is unknown.
 */
public record AuditEntry(UUID queryId, Instant createdAt, long userId, String role, String department,
                         boolean crossDepartment, UUID sessionId, String outcome, String cacheHit, String route,
                         String decidedBy, Float confidence, List<String> paths, List<String> cancelled,
                         String writtenBy, String question, String rewritten, String sql, Long campaignId,
                         Integer sqlAttempts, Integer resultRows, List<Long> citedDocIds, Integer modelCalls,
                         Integer inputTokens, Integer outputTokens, @JsonRawValue String tokens, String costUsd,
                         int totalMs, @JsonRawValue String timingsMs, String rating, String comment) {
}
