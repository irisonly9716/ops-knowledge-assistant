package com.opsassistant.backend.outbox;

import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.Instant;
import lombok.Getter;
import lombok.NoArgsConstructor;

/**
 * One row of the outbox table: a message waiting to be published to Kafka, written in the same database
 * transaction as the business change it announces (see db/migration/V6__outbox.sql).
 */
@Entity
@Table(name = "outbox")
@Getter
@NoArgsConstructor
public class OutboxMessage {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    private String topic;
    private String messageKey;
    private String payload;
    private Instant createdAt;
    private Instant publishedAt;
    private int attempts;
    private String lastError;

    public OutboxMessage(String topic, String messageKey, String payload) {
        this.topic = topic;
        this.messageKey = messageKey;
        this.payload = payload;
        this.createdAt = Instant.now();
    }

    public void markPublished() {
        publishedAt = Instant.now();
        lastError = null;
    }

    public void recordFailure(String error) {
        attempts++;
        lastError = error;
    }
}
