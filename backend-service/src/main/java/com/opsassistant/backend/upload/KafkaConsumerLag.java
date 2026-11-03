package com.opsassistant.backend.upload;

import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.function.Function;
import java.util.stream.Collectors;
import org.apache.kafka.clients.admin.Admin;
import org.apache.kafka.clients.admin.AdminClientConfig;
import org.apache.kafka.clients.admin.ListOffsetsResult.ListOffsetsResultInfo;
import org.apache.kafka.clients.admin.OffsetSpec;
import org.apache.kafka.clients.consumer.OffsetAndMetadata;
import org.apache.kafka.common.TopicPartition;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

/**
 * Reads a group's lag from Kafka. A client is created per read and closed again: reconciliation only asks when an
 * upload looks stuck, which should be rare, and an idle client would keep logging connection warnings while Kafka
 * is down.
 *
 * A position below the oldest retained message counts as that message: what retention deleted can't be read
 * anymore, so it isn't waiting. A group with no committed offset counts from the oldest message too, so a consumer
 * that never ran shows up as lag rather than as "nothing left to do".
 */
@Component
public class KafkaConsumerLag implements ConsumerLag {

    private static final int TIMEOUT_MS = 10_000;

    private final Map<String, Object> config;

    public KafkaConsumerLag(@Value("${spring.kafka.bootstrap-servers}") String bootstrapServers) {
        this.config = Map.of(
                AdminClientConfig.BOOTSTRAP_SERVERS_CONFIG, bootstrapServers,
                AdminClientConfig.REQUEST_TIMEOUT_MS_CONFIG, TIMEOUT_MS / 2,
                AdminClientConfig.DEFAULT_API_TIMEOUT_MS_CONFIG, TIMEOUT_MS);
    }

    @Override
    public Progress read(String consumerGroup, String topic) {
        try (Admin admin = Admin.create(config)) {
            List<TopicPartition> partitions = admin.describeTopics(List.of(topic)).allTopicNames()
                    .get(TIMEOUT_MS, TimeUnit.MILLISECONDS).get(topic).partitions().stream()
                    .map(partition -> new TopicPartition(topic, partition.partition()))
                    .toList();
            Map<TopicPartition, OffsetAndMetadata> committed = admin.listConsumerGroupOffsets(consumerGroup)
                    .partitionsToOffsetAndMetadata().get(TIMEOUT_MS, TimeUnit.MILLISECONDS);
            Map<TopicPartition, ListOffsetsResultInfo> oldest = offsets(admin, partitions, OffsetSpec.earliest());
            Map<TopicPartition, ListOffsetsResultInfo> newest = offsets(admin, partitions, OffsetSpec.latest());

            long lag = 0;
            long position = 0;
            for (TopicPartition partition : partitions) {
                long first = oldest.get(partition).offset();
                OffsetAndMetadata offset = committed.get(partition);
                long at = offset == null ? first : Math.max(offset.offset(), first);
                lag += newest.get(partition).offset() - at;
                position += at;
            }
            return new Progress(lag, position);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("interrupted while reading the lag of " + consumerGroup, e);
        } catch (ExecutionException | TimeoutException e) {
            throw new IllegalStateException("can't read the lag of " + consumerGroup + ": " + e, e);
        }
    }

    private static Map<TopicPartition, ListOffsetsResultInfo> offsets(Admin admin, List<TopicPartition> partitions, OffsetSpec spec)
            throws InterruptedException, ExecutionException, TimeoutException {
        Map<TopicPartition, OffsetSpec> request = partitions.stream().collect(Collectors.toMap(Function.identity(), partition -> spec));
        return admin.listOffsets(request).all().get(TIMEOUT_MS, TimeUnit.MILLISECONDS);
    }
}
