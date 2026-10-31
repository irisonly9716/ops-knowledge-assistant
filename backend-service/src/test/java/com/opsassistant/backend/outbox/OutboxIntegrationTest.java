package com.opsassistant.backend.outbox;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import com.opsassistant.backend.upload.ContentKind;
import com.opsassistant.backend.upload.DocType;
import com.opsassistant.backend.upload.Source;
import com.opsassistant.backend.upload.Upload;
import com.opsassistant.backend.upload.UploadEvent;
import com.opsassistant.backend.upload.UploadRepository;
import com.opsassistant.backend.upload.UploadService;
import java.sql.Connection;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;
import javax.sql.DataSource;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Assumptions;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.Timeout;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.support.SendResult;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.context.bean.override.mockito.MockitoSpyBean;

/**
 * The outbox against the real Postgres (docker compose): the transaction that makes it work, and the
 * row locking of the relay. Kafka is a Mockito fake; the scheduler is switched off and the relay is
 * called directly.
 *
 * It runs against the development database, so it only touches rows it creates (keys starting with
 * "outbox-it-") and deletes them afterwards. It refuses to run while real messages are waiting in the
 * outbox, because the relay under test would mark them published without really sending them.
 * Run with: .\mvnw.cmd test -DexcludedGroups=none -Dgroups=integration
 */
@Tag("integration")
@SpringBootTest(properties = "outbox.relay.enabled=false")
class OutboxIntegrationTest {

    @Autowired
    UploadService uploadService;
    @Autowired
    OutboxRelay relay;
    @Autowired
    UploadRepository uploadRepository;
    @MockitoSpyBean
    OutboxRepository outboxRepository;
    @MockitoBean
    KafkaTemplate<String, String> kafkaTemplate;
    @Autowired
    JdbcTemplate jdbc;
    @Autowired
    DataSource dataSource;

    private final String prefix = "outbox-it-" + UUID.randomUUID().toString().substring(0, 8);
    private Long uploaderId;

    @BeforeEach
    void setUp() {
        Integer waiting = jdbc.queryForObject("SELECT count(*) FROM outbox WHERE published_at IS NULL", Integer.class);
        Assumptions.assumeTrue(waiting == 0, "real messages are waiting in the outbox; not touching them");
        List<Long> users = jdbc.queryForList("SELECT user_id FROM users WHERE role = 'staff' LIMIT 1", Long.class);
        Assumptions.assumeFalse(users.isEmpty(), "needs the mock data (a staff user)");
        uploaderId = users.get(0);
    }

    @AfterEach
    void cleanUp() {
        jdbc.update("DELETE FROM outbox WHERE message_key LIKE ?", prefix + "%");
        jdbc.update("DELETE FROM uploads WHERE storage_key LIKE ?", prefix + "%");
    }

    @Test
    void the_upload_and_its_event_are_committed_together() {
        UUID uploadId = UUID.randomUUID();

        uploadService.record(upload(uploadId), event(uploadId));

        assertThat(uploadRepository.findById(uploadId)).isPresent();
        assertThat(jdbc.queryForObject("SELECT count(*) FROM outbox WHERE message_key = ? AND published_at IS NULL",
                Integer.class, prefix + uploadId)).isEqualTo(1);
    }

    /**
     * The whole point of the outbox: if the event can't be written, the upload isn't either. Without the
     * transaction around both writes, the uploads row would already be committed here, stuck in received
     * with no event behind it.
     */
    @Test
    void if_the_outbox_write_fails_the_upload_is_not_recorded_either() {
        UUID uploadId = UUID.randomUUID();
        doThrow(new RuntimeException("outbox write failed")).when(outboxRepository).save(any());

        assertThatThrownBy(() -> uploadService.record(upload(uploadId), event(uploadId)))
                .hasMessageContaining("outbox write failed");

        assertThat(uploadRepository.findById(uploadId)).isEmpty();
    }

    @Test
    void the_relay_publishes_waiting_messages_and_marks_them() {
        long id = outboxRow("a");
        CompletableFuture<SendResult<String, String>> ok = succeeded();
        when(kafkaTemplate.send(anyString(), anyString(), anyString())).thenReturn(ok);

        assertThat(relay.publishBatch()).isEqualTo(1);

        assertThat(jdbc.queryForObject("SELECT published_at IS NOT NULL FROM outbox WHERE id = ?", Boolean.class, id)).isTrue();
    }

    /**
     * Two relays (say, two backend instances) must not publish the same row at once. A row another
     * transaction holds is skipped -- not waited for -- and the rest of the backlog moves on.
     * The timeout turns "waits for the lock forever" (FOR UPDATE without SKIP LOCKED) into a failure instead
     * of a hanging build.
     */
    @Test
    @Timeout(value = 20, unit = TimeUnit.SECONDS, threadMode = Timeout.ThreadMode.SEPARATE_THREAD)
    void a_row_locked_by_another_relay_is_skipped_not_waited_for() throws Exception {
        long locked = outboxRow("locked");
        long free = outboxRow("free");
        CompletableFuture<SendResult<String, String>> ok = succeeded();
        when(kafkaTemplate.send(anyString(), anyString(), anyString())).thenReturn(ok);

        try (Connection other = dataSource.getConnection()) {
            other.setAutoCommit(false);
            other.createStatement().execute("SELECT id FROM outbox WHERE id = " + locked + " FOR UPDATE");

            assertThat(relay.publishBatch()).isEqualTo(1);   // returns at once although "locked" is busy

            other.rollback();
        }
        assertThat(jdbc.queryForObject("SELECT published_at IS NOT NULL FROM outbox WHERE id = ?", Boolean.class, locked)).isFalse();
        assertThat(jdbc.queryForObject("SELECT published_at IS NOT NULL FROM outbox WHERE id = ?", Boolean.class, free)).isTrue();
    }

    private Upload upload(UUID uploadId) {
        Upload upload = new Upload();
        upload.setUploadId(uploadId);
        upload.setSource("manual");
        upload.setUploadedBy(uploaderId);
        upload.setContentKind("document");
        upload.setDocType("sop");
        upload.setDepartment("ALL");
        upload.setTitle("Outbox integration test " + uploadId);
        upload.setOriginalFilename("test.md");
        upload.setStorageKey(prefix + uploadId + "-test.md");
        upload.setCreatedAt(Instant.now());
        upload.changeStatus(Upload.RECEIVED, null);
        return upload;
    }

    private UploadEvent event(UUID uploadId) {
        // The message key is the upload id; prefixing it here is only so cleanUp() can find the test's rows
        return new UploadEvent(prefix + uploadId, prefix + uploadId + "-test.md", ContentKind.DOCUMENT, DocType.SOP,
                null, "ALL", null, null, Source.MANUAL);
    }

    private long outboxRow(String name) {
        return outboxRepository.save(new OutboxMessage("test-topic", prefix + name, "{}")).getId();
    }

    @SuppressWarnings("unchecked")
    private static CompletableFuture<SendResult<String, String>> succeeded() {
        SendResult<String, String> result = mock(SendResult.class);
        return CompletableFuture.completedFuture(result);
    }
}
