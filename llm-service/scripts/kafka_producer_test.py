"""Day 3 验证脚本:往 document-upload-events 发一条测试消息。

跟 app/consumers/ 里"真正的"生产逻辑分开 —— 这里只是确认 Python 能连上 Kafka、
消息能发出去,不接业务逻辑。跑法:

    cd llm-service
    uv run python scripts/kafka_producer_test.py
"""
import json
import sys

from confluent_kafka import Producer

BOOTSTRAP_SERVERS = "localhost:9092"   # 宿主机上跑的脚本,连 EXTERNAL listener
TOPIC = "document-upload-events"


def delivery_report(err, msg):
    """Kafka 是异步发送的:调用 produce() 只是把消息放进本地缓冲区,
    真正发到 broker 后(或失败后)才会回调这个函数,所以不能只看 produce() 有没有报错。
    """
    if err is not None:
        print(f"❌ 发送失败: {err}", file=sys.stderr)
    else:
        print(f"✅ 发送成功 -> topic={msg.topic()} partition={msg.partition()} offset={msg.offset()}")


def main():
    producer = Producer({"bootstrap.servers": BOOTSTRAP_SERVERS})

    message = {
        "doc_id": 1,
        "path": "/tmp/test.md",
        "department": "美妆运营",
        "category": "风控",
        "source": "manual",   # manual = 员工手动上传;auto = 未来定时任务自动摄取
    }

    producer.produce(
        TOPIC,
        value=json.dumps(message, ensure_ascii=False).encode("utf-8"),
        callback=delivery_report,
    )

    # flush() 阻塞直到所有缓冲区里的消息都发出去(或超时),
    # 脚本跑完就退出的场景必须调用,不然消息可能还没真正发走进程就没了。
    producer.flush(timeout=10)


if __name__ == "__main__":
    main()
