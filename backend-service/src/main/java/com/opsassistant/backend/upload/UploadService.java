package com.opsassistant.backend.upload;

import com.opsassistant.backend.outbox.OutboxMessage;
import com.opsassistant.backend.outbox.OutboxRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.ObjectMapper;

/**
 * Records an accepted upload: the uploads row and the outbox row carrying its event, in one transaction.
 *
 * Either both rows are committed or neither is, so an upload can never be recorded without its event, or
 * the event exist without the upload. Publishing to Kafka happens later, in OutboxRelay; the upload request
 * doesn't wait for Kafka and doesn't fail when Kafka is down.
 */
@Service
@RequiredArgsConstructor
public class UploadService {

    private final UploadRepository uploadRepository;
    private final OutboxRepository outboxRepository;
    private final ObjectMapper objectMapper; // the Jackson instance Spring Boot auto-configures

    @Value("${document.upload-topic}")
    private String uploadTopic;

    @Transactional
    public void record(Upload upload, UploadEvent event) {
        // Serialized here, inside the transaction: if it fails, nothing is written. JacksonException is
        // unchecked, so it simply rolls the transaction back.
        // The ObjectMapper is tools.jackson.*, not the com.fasterxml.jackson.* of most tutorials: Spring
        // Boot 4 switched to Jackson 3. Annotations such as @JsonValue stayed in
        // com.fasterxml.jackson.annotation.*, so mixing the two package names is expected
        String payload = objectMapper.writeValueAsString(event);
        uploadRepository.save(upload);
        // uploadId as the key: messages of one upload always land in the same partition, so if an upload
        // ever produces several messages (say, chunking progress events) their order holds
        outboxRepository.save(new OutboxMessage(uploadTopic, event.uploadId(), payload));
    }
}
