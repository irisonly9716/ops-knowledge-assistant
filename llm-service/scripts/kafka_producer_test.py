"""手动往 document-upload-events 发一条【符合契约的】上传事件——不经过 Java 后端,
方便单独验证消费者。跑法(注意是 -m,不是直接给文件路径,原因见下方说明):

    cd llm-service
    uv run python -m scripts.kafka_producer_test

这个脚本最初发的是随手编的消息(doc_id / path 字段),后来契约定了(见
contracts/upload-event.examples.json),那种消息现在会被消费者正确地扔进死信。
所以这里改成:先在上传目录里写一个真实文件,再发一条指向它的合法事件——
消费者应该能收到并读到文件内容,整条 Python 侧的链路一次验证完。

为什么用 `-m scripts.xxx` 而不是 `python scripts/xxx.py`:
后者会把 sys.path[0] 设成 scripts/ 目录本身,`import app.config` 找不到 app 包;
用 `-m` 时 Python 以当前目录(llm-service/)为根,app/ 和 scripts/ 都在同一层,才能正常 import。
"""
import json
import sys
import uuid
from pathlib import Path

from confluent_kafka import Producer

from app.config import settings

BOOTSTRAP_SERVERS = settings.kafka_bootstrap_servers   # 宿主机上跑的脚本,连 EXTERNAL listener
TOPIC = settings.doc_upload_topic


def delivery_report(err, msg):
    """Kafka 是异步发送的:调用 produce() 只是把消息放进本地缓冲区,
    真正发到 broker 后(或失败后)才会回调这个函数,所以不能只看 produce() 有没有报错。
    """
    if err is not None:
        print(f"❌ 发送失败: {err}", file=sys.stderr)
    else:
        print(f"✅ 发送成功 -> topic={msg.topic()} partition={msg.partition()} offset={msg.offset()}")


def main():
    upload_id = str(uuid.uuid4())
    storage_key = f"{upload_id}-smoke-test.md"

    # 先把文件放进上传目录(生产环境里这一步是 backend-service 做的)
    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    (upload_dir / storage_key).write_text("# Kafka 冒烟测试\n\n这是 kafka_producer_test.py 写进上传目录的文件。\n", encoding="utf-8")

    producer = Producer({"bootstrap.servers": BOOTSTRAP_SERVERS})
    event = {  # 字段名和取值必须跟 contracts/upload-event.examples.json 一致(camelCase)
        "uploadId": upload_id,
        "storageKey": storage_key,
        "contentKind": "document",
        "docType": "sop",
        "tableType": None,
        "department": "ALL",
        "campaignId": None,
        "category": "冒烟测试",
        "source": "manual",
    }
    producer.produce(
        TOPIC,
        key=upload_id,
        value=json.dumps(event, ensure_ascii=False).encode("utf-8"),
        callback=delivery_report,
    )

    # flush() 阻塞直到所有缓冲区里的消息都发出去(或超时),
    # 脚本跑完就退出的场景必须调用,不然消息可能还没真正发走进程就没了。
    producer.flush(timeout=10)


if __name__ == "__main__":
    main()
