package com.opsassistant.backend.upload;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.Properties;
import java.util.UUID;
import org.apache.kafka.clients.admin.Admin;
import org.apache.kafka.clients.admin.AdminClientConfig;
import org.apache.kafka.clients.admin.NewTopic;
import org.apache.kafka.clients.consumer.ConsumerConfig;
import org.apache.kafka.clients.consumer.KafkaConsumer;
import org.apache.kafka.clients.consumer.OffsetAndMetadata;
import org.apache.kafka.clients.producer.KafkaProducer;
import org.apache.kafka.clients.producer.ProducerConfig;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.apache.kafka.common.TopicPartition;
import org.apache.kafka.common.serialization.StringDeserializer;
import org.apache.kafka.common.serialization.StringSerializer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Assumptions;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;

/**
 * Against the real Postgres and the real broker: which rows count as stuck, what publishing again writes, and that
 * the lag read from Kafka means what the decisions assume. Only touches rows and topics it creates.
 * Run with: .\mvnw.cmd test -DexcludedGroups=none -Dgroups=integration
 */
@Tag("integration")
@SpringBootTest(properties = "outbox.relay.enabled=false")
class ReconciliationIntegrationTest {

    private static final int MAX = 3;

    @Autowired
    UploadRepository uploadRepository;
    @Autowired
    UploadService uploadService;
    @Autowired
    ConsumerLag consumerLag;
    @Autowired
    JdbcTemplate jdbc;
    @Value("${spring.kafka.bootstrap-servers}")
    String bootstrapServers;

    private final String prefix = "reconcile-it-" + UUID.randomUUID().toString().substring(0, 8);
    private final Instant cutoff = Instant.now().minus(Duration.ofMinutes(5));
    private final Instant longAgo = Instant.now().minus(Duration.ofHours(1));
    private Long uploaderId;

    @BeforeEach
    void setUp() {
        List<Long> users = jdbc.queryForList("SELECT user_id FROM users WHERE role = 'staff' LIMIT 1", Long.class);
        Assumptions.assumeFalse(users.isEmpty(), "needs the mock data (a staff user)");
        uploaderId = users.get(0);
    }

    @AfterEach
    void cleanUp() {
        jdbc.update("DELETE FROM outbox WHERE message_key IN (SELECT upload_id::text FROM uploads WHERE storage_key LIKE ?)", prefix + "%");
        jdbc.update("DELETE FROM uploads WHERE storage_key LIKE ?", prefix + "%");
    }

    private UUID row(String status, Instant updatedAt) {
        UUID id = UUID.randomUUID();
        jdbc.update("""
                INSERT INTO uploads (upload_id, source, uploaded_by, content_kind, doc_type, department, title,
                                     original_filename, storage_key, status, updated_at)
                VALUES (?, 'manual', ?, 'document', 'sop', 'Beauty', ?, 'x.md', ?, ?, ?::timestamptz)""",
                id, uploaderId, "Reconcile " + id, prefix + id, status, updatedAt.toString());
        return id;
    }

    private List<UUID> found() {
        return uploadRepository.findStuck(cutoff, 1000).stream().map(Upload::getUploadId).toList();
    }

    private Map<String, Object> state(UUID id) {
        return jdbc.queryForMap("SELECT status, error, republish_count, last_republished_at FROM uploads WHERE upload_id = ?", id);
    }

    private int events(UUID id) {
        Integer count = jdbc.queryForObject("SELECT count(*) FROM outbox WHERE message_key = ?", Integer.class, id.toString());
        return count == null ? 0 : count;
    }

    // ------------------------------------------------------------------ which rows count as stuck

    @Test
    void unfinished_uploads_older_than_the_limit_are_stuck() {
        UUID received = row("received", longAgo);
        UUID processing = row("processing", longAgo);

        assertThat(found()).contains(received, processing);
    }

    @Test
    void a_recent_upload_is_not_stuck() {
        UUID recent = row("received", Instant.now());

        assertThat(found()).doesNotContain(recent);
    }

    @Test
    void uploads_that_ended_or_never_started_are_not_stuck() {
        UUID done = row("done", longAgo);
        UUID failed = row("failed", longAgo);
        UUID waiting = row("pending_approval", longAgo);   // no event yet, on purpose
        UUID rejected = row("rejected", longAgo);

        assertThat(found()).doesNotContain(done, failed, waiting, rejected);
    }

    @Test
    void an_upload_whose_event_is_still_waiting_to_be_sent_is_not_stuck() {
        UUID id = row("received", longAgo);
        jdbc.update("INSERT INTO outbox (topic, message_key, payload) VALUES ('t', ?, '{}')", id.toString());

        assertThat(found()).doesNotContain(id);
    }

    @Test
    void an_upload_whose_event_was_sent_can_be_stuck() {
        UUID id = row("received", longAgo);
        jdbc.update("INSERT INTO outbox (topic, message_key, payload, published_at) VALUES ('t', ?, '{}', now())", id.toString());

        assertThat(found()).contains(id);
    }

    @Test
    void an_upload_published_again_recently_gets_time_before_it_counts_as_stuck_again() {
        UUID id = row("processing", longAgo);
        jdbc.update("UPDATE uploads SET last_republished_at = now() WHERE upload_id = ?", id);

        assertThat(found()).doesNotContain(id);
    }

    @Test
    void the_batch_limit_holds() {
        row("received", longAgo);
        row("received", longAgo);

        assertThat(uploadRepository.findStuck(cutoff, 1)).hasSize(1);
    }

    // ------------------------------------------------------------------ publishing again

    @Test
    void publishing_again_records_it_and_writes_the_event_in_the_outbox() {
        UUID id = row("processing", longAgo);

        assertThat(uploadService.republishIfStuck(id, cutoff, MAX)).isEqualTo(UploadService.Republish.REPUBLISHED);

        Map<String, Object> state = state(id);
        assertThat(state.get("status")).isEqualTo("processing");
        assertThat(state.get("republish_count")).isEqualTo(1);
        assertThat(state.get("last_republished_at")).isNotNull();
        assertThat(events(id)).isEqualTo(1);
    }

    @Test
    void after_publishing_again_it_is_not_published_a_second_time_until_the_limit_has_passed() {
        UUID id = row("received", longAgo);
        uploadService.republishIfStuck(id, cutoff, MAX);

        assertThat(uploadService.republishIfStuck(id, cutoff, MAX)).isEqualTo(UploadService.Republish.NOTHING_TO_DO);

        assertThat(events(id)).isEqualTo(1);
    }

    @Test
    void at_the_limit_it_is_marked_failed_with_the_reason_and_nothing_more_is_published() {
        UUID id = row("processing", longAgo);
        jdbc.update("UPDATE uploads SET republish_count = ? WHERE upload_id = ?", MAX, id);

        assertThat(uploadService.republishIfStuck(id, cutoff, MAX)).isEqualTo(UploadService.Republish.GAVE_UP);

        Map<String, Object> state = state(id);
        assertThat(state.get("status")).isEqualTo("failed");
        assertThat((String) state.get("error")).contains("published again 3 times");
        assertThat(events(id)).isZero();
    }

    @Test
    void one_short_of_the_limit_it_is_still_published() {
        UUID id = row("processing", longAgo);
        jdbc.update("UPDATE uploads SET republish_count = ? WHERE upload_id = ?", MAX - 1, id);

        assertThat(uploadService.republishIfStuck(id, cutoff, MAX)).isEqualTo(UploadService.Republish.REPUBLISHED);
    }

    @Test
    void an_upload_that_finished_in_the_meantime_is_left_alone() {
        UUID id = row("done", longAgo);

        assertThat(uploadService.republishIfStuck(id, cutoff, MAX)).isEqualTo(UploadService.Republish.NOTHING_TO_DO);

        assertThat(events(id)).isZero();
    }

    @Test
    void an_upload_that_moved_recently_is_left_alone() {
        UUID id = row("processing", Instant.now());

        assertThat(uploadService.republishIfStuck(id, cutoff, MAX)).isEqualTo(UploadService.Republish.NOTHING_TO_DO);
    }

    @Test
    void an_upload_that_no_longer_exists_is_left_alone() {
        assertThat(uploadService.republishIfStuck(UUID.randomUUID(), cutoff, MAX)).isEqualTo(UploadService.Republish.NOTHING_TO_DO);
    }

    @Test
    void publishing_again_does_not_touch_the_consumers_delivery_count() {
        UUID id = row("processing", longAgo);
        jdbc.update("UPDATE uploads SET delivery_count = 2 WHERE upload_id = ?", id);

        uploadService.republishIfStuck(id, cutoff, MAX);

        assertThat(jdbc.queryForObject("SELECT delivery_count FROM uploads WHERE upload_id = ?", Integer.class, id)).isEqualTo(2);
    }

    // ------------------------------------------------------------------ the real lag

    @Test
    void the_lag_read_from_a_real_broker_means_what_the_decisions_assume() throws Exception {
        Properties adminConfig = new Properties();
        adminConfig.put(AdminClientConfig.BOOTSTRAP_SERVERS_CONFIG, bootstrapServers);
        adminConfig.put(AdminClientConfig.REQUEST_TIMEOUT_MS_CONFIG, 3000);
        adminConfig.put(AdminClientConfig.DEFAULT_API_TIMEOUT_MS_CONFIG, 5000);
        String topic = prefix;
        String group = prefix + "-group";
        try (Admin admin = Admin.create(adminConfig)) {
            try {
                admin.createTopics(List.of(new NewTopic(topic, 2, (short) 1))).all().get();
            } catch (Exception e) {
                assumeTrue(false, "needs the docker compose Kafka: " + e.getMessage());
            }
            try {
                // An empty topic and a group that never ran: nothing is waiting
                assertThat(consumerLag.read(group, topic)).isEqualTo(new ConsumerLag.Progress(0, 0));

                send(topic, 3);
                // A group that never ran counts from the oldest message, so it shows as lag and not as "all done"
                assertThat(consumerLag.read(group, topic).lag()).isEqualTo(3);

                // A message being worked on isn't committed yet, so it still counts
                commitAll(topic, group, 1);
                ConsumerLag.Progress part = consumerLag.read(group, topic);
                assertThat(part.lag()).isEqualTo(2);
                assertThat(part.committed()).isEqualTo(1);

                // Everything committed: the only case in which the decisions conclude that a message is gone
                commitAll(topic, group, 3);
                assertThat(consumerLag.read(group, topic)).isEqualTo(new ConsumerLag.Progress(0, 3));
            } finally {
                admin.deleteTopics(List.of(topic)).all().get();
            }
        }
    }

    private void send(String topic, int count) throws Exception {
        Properties config = new Properties();
        config.put(ProducerConfig.BOOTSTRAP_SERVERS_CONFIG, bootstrapServers);
        config.put(ProducerConfig.KEY_SERIALIZER_CLASS_CONFIG, StringSerializer.class);
        config.put(ProducerConfig.VALUE_SERIALIZER_CLASS_CONFIG, StringSerializer.class);
        try (KafkaProducer<String, String> producer = new KafkaProducer<>(config)) {
            for (int i = 0; i < count; i++) {
                producer.send(new ProducerRecord<>(topic, 0, "key", "value")).get();   // one partition, so offsets are simple
            }
        }
    }

    private void commitAll(String topic, String group, long offset) {
        Properties config = new Properties();
        config.put(ConsumerConfig.BOOTSTRAP_SERVERS_CONFIG, bootstrapServers);
        config.put(ConsumerConfig.GROUP_ID_CONFIG, group);
        config.put(ConsumerConfig.KEY_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class);
        config.put(ConsumerConfig.VALUE_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class);
        TopicPartition partition = new TopicPartition(topic, 0);
        try (KafkaConsumer<String, String> consumer = new KafkaConsumer<>(config)) {
            consumer.assign(List.of(partition));
            consumer.commitSync(Map.of(partition, new OffsetAndMetadata(offset)));
        }
    }
}
