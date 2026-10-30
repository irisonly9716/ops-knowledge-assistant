"""Create the Kafka topics the project needs, idempotently: a topic that already exists is skipped.

On a new machine, after `docker compose down -v`, or later in CI/deployment, one run creates
every topic the project needs, instead of digging up the kafka-topics.sh arguments again.

Run (why -m: see kafka_producer_test.py):
    cd llm-service
    uv run python -m scripts.init_topics

To add a topic, add one line to TOPICS below; this list is the single authoritative answer to
"which event streams does this project have". Topic names come from settings in app/config.py,
never re-typed here.
"""
from confluent_kafka.admin import AdminClient, NewTopic

from app.config import settings

BOOTSTRAP_SERVERS = settings.kafka_bootstrap_servers

TOPICS = [
    NewTopic(settings.doc_upload_topic, num_partitions=1, replication_factor=1),
    # Dead-letter topic: messages the consumer can't process (malformed / retries exhausted) are
    # parked here, so they neither block the main flow nor get lost. The name is derived from the
    # main topic (see app/config.py); the consumer and this script share that one source.
    NewTopic(settings.doc_upload_dlq_topic, num_partitions=1, replication_factor=1),
    # A semantically different kind of event would get its own line here, for example:
    # NewTopic("document-processing-failed", num_partitions=1, replication_factor=1),
]


def main():
    admin = AdminClient({"bootstrap.servers": BOOTSTRAP_SERVERS})

    existing = set(admin.list_topics(timeout=10).topics.keys())
    to_create = [t for t in TOPICS if t.topic not in existing]

    if not to_create:
        print("All topics already exist, nothing to create:")
        for t in TOPICS:
            print(f"  - {t.topic}")
        return

    futures = admin.create_topics(to_create)
    for topic, future in futures.items():
        try:
            future.result()  # blocks until the topic really exists; a failure raises here
            print(f"Created: {topic}")
        except Exception as e:
            print(f"FAILED to create {topic}: {e}")


if __name__ == "__main__":
    main()
