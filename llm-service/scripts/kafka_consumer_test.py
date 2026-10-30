"""Debug script: read one message from document-upload-events, print it and exit.

Run (publish one with the producer script first; why -m: see kafka_producer_test.py):

    cd llm-service
    uv run python -m scripts.kafka_consumer_test
"""
import json

from confluent_kafka import Consumer

from app.config import settings

BOOTSTRAP_SERVERS = settings.kafka_bootstrap_servers
TOPIC = settings.doc_upload_topic

# Consumers with the same group.id share the partitions and the consumption progress (offsets).
# A new group.id is "a brand-new reader from the start", unaffected by how far others got --
# so this debug script uses its own group name instead of the real consumer service's group.id.
GROUP_ID = "dev-smoke-test"


def main():
    consumer = Consumer({
        "bootstrap.servers": BOOTSTRAP_SERVERS,
        "group.id": GROUP_ID,
        "auto.offset.reset": "earliest",   # the first time this group consumes, start from the earliest message
    })
    consumer.subscribe([TOPIC])

    print(f"Waiting for a message on {TOPIC} (up to 10 seconds)...")
    try:
        msg = consumer.poll(timeout=10.0)   # poll asks the broker once for new messages, blocking up to 10 seconds
        if msg is None:
            print("No message within 10 seconds; check that the producer script ran and the topic name is right")
            return
        if msg.error():
            print(f"Consume error: {msg.error()}")
            return

        # utf-8-sig strips a leading UTF-8 BOM if there is one and is identical to utf-8 otherwise;
        # sturdier than plain "utf-8", since files from Windows often carry a BOM.
        payload = json.loads(msg.value().decode("utf-8-sig"))
        print(f"Received partition={msg.partition()} offset={msg.offset()}")
        print(json.dumps(payload, indent=2))
    finally:
        consumer.close()   # close explicitly: tells the broker this consumer left and triggers a partition rebalance


if __name__ == "__main__":
    main()
