package com.opsassistant.backend.upload;

/** How far a consumer group has come on a topic. An interface so the decisions can be tested without Kafka. */
public interface ConsumerLag {

    /**
     * @param lag       messages the group hasn't finished. While a message is being processed its offset isn't
     *                  committed yet, so 0 means nothing is waiting and nothing is in flight
     * @param committed sum of the positions over the partitions: if it stops moving, the group isn't advancing
     */
    record Progress(long lag, long committed) {
    }

    /** Throws if Kafka can't answer: without an answer, nobody may conclude that a message is gone. */
    Progress read(String consumerGroup, String topic);
}
