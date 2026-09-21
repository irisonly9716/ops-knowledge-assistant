"""幂等地创建项目需要的 Kafka topic —— topic 已存在就跳过,不会重复报错。

新机器 / `docker compose down -v` 重来后,或者以后接 CI/部署,跑一次这个就把
项目需要的 topic 都建好,不用再翻记录去手敲 kafka-topics.sh 的一堆参数。

跑法(用 -m 的原因见 kafka_producer_test.py):
    cd llm-service
    uv run python -m scripts.init_topics

新增 topic 只需要往下面 TOPICS 列表加一行,这里就是"这个项目一共有哪些事件流"的
唯一权威清单;topic 名字统一从 app/config.py 的 settings 读,不在这里重新拼字符串。
"""
from confluent_kafka.admin import AdminClient, NewTopic

from app.config import settings

BOOTSTRAP_SERVERS = settings.kafka_bootstrap_servers

TOPICS = [
    NewTopic(settings.doc_upload_topic, num_partitions=1, replication_factor=1),
    # 死信 topic:消费者处理不了的消息(格式坏了 / 重试用尽)转存在这里,不阻塞主流程也不丢。
    # 名字从主 topic 派生(见 app/config.py),消费者和这个脚本用的是同一个来源。
    NewTopic(settings.doc_upload_dlq_topic, num_partitions=1, replication_factor=1),
    # 以后如果有语义上不同的事件种类,在这里加新的一行,比如:
    # NewTopic("document-processing-failed", num_partitions=1, replication_factor=1),
]


def main():
    admin = AdminClient({"bootstrap.servers": BOOTSTRAP_SERVERS})

    existing = set(admin.list_topics(timeout=10).topics.keys())
    to_create = [t for t in TOPICS if t.topic not in existing]

    if not to_create:
        print("所有 topic 都已存在,无需创建:")
        for t in TOPICS:
            print(f"  - {t.topic}")
        return

    futures = admin.create_topics(to_create)
    for topic, future in futures.items():
        try:
            future.result()  # 阻塞等待这个 topic 真正建完;失败会在这里抛异常
            print(f"✅ 创建成功: {topic}")
        except Exception as e:
            print(f"❌ 创建失败: {topic} -> {e}")


if __name__ == "__main__":
    main()
