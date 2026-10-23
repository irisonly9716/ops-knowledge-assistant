"""调试脚本:从 document-upload-events 读消息,打印出来就退出。

跑法(先跑 producer 发一条,再跑这个读;用 -m 的原因见 kafka_producer_test.py):

    cd llm-service
    uv run python -m scripts.kafka_consumer_test
"""
import json

from confluent_kafka import Consumer

from app.config import settings

BOOTSTRAP_SERVERS = settings.kafka_bootstrap_servers
TOPIC = settings.doc_upload_topic

# 同一个 group.id 里的多个 consumer 会分摊 partition、共享消费进度(offset)。
# 换一个新的 group.id 相当于"从头当一个新读者",不受之前消费到哪里的影响 ——
# 调试脚本用一个独立的组名,不跟真正的消费者服务混用同一个 group.id。
GROUP_ID = "dev-smoke-test"


def main():
    consumer = Consumer({
        "bootstrap.servers": BOOTSTRAP_SERVERS,
        "group.id": GROUP_ID,
        "auto.offset.reset": "earliest",   # 这个组第一次消费时,从最早的消息开始读
    })
    consumer.subscribe([TOPIC])

    print(f"等待 {TOPIC} 的消息(最多等 10 秒)...")
    try:
        msg = consumer.poll(timeout=10.0)   # poll 是"问一次 broker 有没有新消息",阻塞最多 10 秒
        if msg is None:
            print("⚠️ 10 秒内没收到消息,确认 producer 脚本先跑过、topic 名对不对")
            return
        if msg.error():
            print(f"❌ 消费出错: {msg.error()}")
            return

        # utf-8-sig: 如果消息开头带 UTF-8 BOM 就自动剥掉,没有 BOM 时跟 utf-8 完全一样,
        # 比死用 "utf-8" 更抗糙——生产上传的文件经常来自 Windows,带 BOM 很常见。
        payload = json.loads(msg.value().decode("utf-8-sig"))
        print(f"✅ 收到消息 partition={msg.partition()} offset={msg.offset()}")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    finally:
        consumer.close()   # 主动关闭,通知 broker 这个 consumer 下线,触发 partition 再平衡


if __name__ == "__main__":
    main()
