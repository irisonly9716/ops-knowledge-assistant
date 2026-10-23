"""document-upload-events 的消费者(独立的 worker 进程)。

运行:  cd llm-service
       uv run python -m app.consumers.upload_consumer
Ctrl+C 会优雅退出:处理完手上这条消息、正常离开消费组,再结束。

为什么是独立进程,不是塞进 FastAPI 的后台线程:
  - 消费者是个长期阻塞的 poll 循环,跟处理 HTTP 请求的 async 事件循环是两种性质的活,
    混在一个进程里要小心线程和事件循环互相干扰
  - 生命周期独立:web 服务重启/扩容不该打断一条正在处理的文档,反过来也一样
  - 可以独立伸缩:同一个消费组多开几个 worker 就分摊分区;上 Docker 时同一个镜像、
    两条不同的启动命令(uvicorn / 这个模块)即可
  - 因为是普通的阻塞循环,用同步的 confluent-kafka 就够,不需要 aiokafka
    (结论:独立进程 + 同步库,最简单也最稳)

投递语义:【至少一次】。关掉自动提交,处理完(或确定进了死信)之后才手动提交 offset。
  处理完但提交前崩溃 -> 重启后这条会被重新投递 -> 处理逻辑必须能容忍重复(见 handlers.py)
  相反的顺序(先提交后处理)是"至多一次":崩溃就丢消息,对文档摄入不可接受。
"""
import json
import logging
import signal
import threading
import time
from collections.abc import Callable
from pathlib import Path

from confluent_kafka import Consumer, KafkaException, Producer
from pydantic import ValidationError

from app.config import settings
from app.consumers.errors import PermanentError
from app.consumers.events import UploadEvent
from app.consumers.handlers import handle_upload

log = logging.getLogger("upload_consumer")

Handler = Callable[[UploadEvent], None]


def parse_event(raw: bytes | None) -> UploadEvent:
    """原始字节 -> UploadEvent。任何一步不对都是 PermanentError(消息本身坏了,重试无意义)。"""
    if raw is None:
        raise PermanentError("消息体为空")
    try:
        # json.loads 接受 bytes,并且能自动处理 UTF-8 BOM——之前见过
        # PowerShell 管道发出来的消息开头会带 BOM,这里天然容忍
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise PermanentError(f"消息不是合法的 UTF-8 JSON: {e}") from e
    try:
        return UploadEvent.model_validate(payload)
    except ValidationError as e:
        problems = "; ".join(f"{'.'.join(map(str, err['loc'])) or '(整体)'}: {err['msg']}" for err in e.errors())
        raise PermanentError(f"消息不符合契约: {problems}") from e


class UploadConsumer:
    """消费循环。consumer / dlq_producer / handler / sleep 都从外面传进来(依赖注入),
    这样测试里可以换成假的——不用真 Kafka,也不用真等退避时间,就能测所有分支。"""

    def __init__(
        self,
        consumer,
        dlq_producer,
        handler: Handler,
        *,
        topic: str,
        dlq_topic: str,
        max_attempts: int = 3,
        backoff_seconds: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._consumer = consumer
        self._dlq = dlq_producer
        self._handler = handler
        self._topic = topic
        self._dlq_topic = dlq_topic
        self._max_attempts = max_attempts
        self._backoff = backoff_seconds
        self._sleep = sleep

    # ------------------------------------------------------------------ 主循环

    def run(self, stop: threading.Event, poll_timeout: float = 1.0) -> None:
        self._consumer.subscribe([self._topic])
        try:
            while not stop.is_set():
                self.poll_once(poll_timeout)  # poll 有超时,所以 stop 被置位后最多等 1 秒就能退出
        finally:
            self._consumer.close()  # 正常离开消费组,让别的成员立刻接手分区,不用等会话超时

    def poll_once(self, timeout: float) -> bool:
        """取一条消息并处理。返回这一轮是否真的处理了一条消息。"""
        msg = self._consumer.poll(timeout)
        if msg is None:
            return False
        if msg.error():
            self._on_kafka_error(msg.error())
            return False
        self._process(msg)
        return True

    def _on_kafka_error(self, err) -> None:
        if err.fatal():
            raise KafkaException(err)
        # 非致命错误(典型:topic 还没建 -> UNKNOWN_TOPIC_OR_PART;broker 暂时连不上)
        # 客户端库自己会重连,这里只记日志并稍等,避免空转刷屏
        log.warning("Kafka 报告了一个非致命错误,稍后继续: %s", err)
        self._sleep(1)

    # ------------------------------------------------------------------ 处理一条消息

    def _process(self, msg) -> None:
        """一条消息一定会走到"成功"或"进死信"两个终点之一,然后才提交 offset。"""
        try:
            event = parse_event(msg.value())
        except PermanentError as e:
            log.error("消息不合法,进死信 (partition=%s offset=%s): %s", msg.partition(), msg.offset(), e)
            self._dead_letter(msg, str(e), attempts=0)
            return

        last_error = "未知错误"
        for attempt in range(1, self._max_attempts + 1):
            try:
                self._handler(event)
            except PermanentError as e:
                log.error("upload_id=%s 永久失败,直接进死信: %s", event.upload_id, e)
                self._dead_letter(msg, str(e), attempts=attempt)
                return
            except Exception as e:  # noqa: BLE001 —— 这里就是要接住"任何"处理失败,分类靠上面的 PermanentError
                last_error = f"{type(e).__name__}: {e}"
                if attempt < self._max_attempts:
                    wait = self._backoff * 2 ** (attempt - 1)
                    log.warning("upload_id=%s 第 %d/%d 次失败,%.1f 秒后重试: %s",
                                event.upload_id, attempt, self._max_attempts, wait, last_error)
                    self._sleep(wait)
                continue
            self._commit(msg)  # 【先处理成功、后提交】——至少一次
            return

        log.error("upload_id=%s 共尝试 %d 次仍失败,进死信: %s", event.upload_id, self._max_attempts, last_error)
        self._dead_letter(msg, last_error, attempts=self._max_attempts)

    def _dead_letter(self, msg, reason: str, attempts: int) -> None:
        """把原始消息(原样字节,不重新序列化)连同失败原因转发到死信 topic,然后提交 offset。

        死信的意义:坏消息既不能无限重试卡住后面的消息(队头阻塞),也不能悄悄丢掉——
        把它挪到旁边保存,主流程继续,人可以之后查看、修复、重放。

        铁律:死信必须【确认写入成功】才能提交 offset。如果死信也写不进去就提交,这条消息
        就凭空消失了。所以写不进去时抛异常让进程崩溃——重启后 offset 没提交,消息会被重新
        投递,宁可停下来也不丢数据。
        """
        errors: list = []

        def on_delivery(err, _msg) -> None:
            if err is not None:
                errors.append(err)

        self._dlq.produce(
            self._dlq_topic,
            key=msg.key(),
            value=msg.value(),
            headers=[
                ("x-error", reason.encode("utf-8")),
                ("x-attempts", str(attempts).encode()),
                ("x-source-topic", str(msg.topic()).encode()),
                ("x-source-partition", str(msg.partition()).encode()),
                ("x-source-offset", str(msg.offset()).encode()),
            ],
            callback=on_delivery,
        )
        still_queued = self._dlq.flush(10)
        if still_queued or errors:
            raise RuntimeError(f"死信写入失败(未送达 {still_queued} 条, 错误 {errors}),不提交 offset,避免消息丢失")
        self._commit(msg)

    def _commit(self, msg) -> None:
        # 同步提交这一条:asynchronous=False 会等到 broker 确认。提交的是 msg.offset()+1
        # ("下一条该从哪读"),不是 msg.offset() 本身
        self._consumer.commit(message=msg, asynchronous=False)


# ---------------------------------------------------------------------- 入口


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    consumer = Consumer({
        "bootstrap.servers": settings.kafka_bootstrap_servers,
        "group.id": settings.doc_consumer_group,
        # 新消费组第一次启动时从最早的消息读起——消费者启动之前上传的文件也要处理。
        # (已经有提交记录的组不受影响,总是从上次提交的位置继续。)
        "auto.offset.reset": "earliest",
        # 关掉自动提交:自动提交是"每隔几秒把已 poll 到的都算作已处理",跟真实处理进度无关,
        # 会造成"还没处理完就被标记已完成"的丢消息。
        "enable.auto.commit": False,
    })
    producer = Producer({"bootstrap.servers": settings.kafka_bootstrap_servers})

    worker = UploadConsumer(
        consumer, producer, handle_upload,
        topic=settings.doc_upload_topic,
        dlq_topic=settings.doc_upload_dlq_topic,
        max_attempts=settings.doc_consumer_max_attempts,
        backoff_seconds=settings.doc_consumer_backoff_seconds,
    )

    stop = threading.Event()

    def request_stop(signum, _frame) -> None:
        log.info("收到停止信号(%s),处理完当前消息后退出", signum)
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    if hasattr(signal, "SIGTERM"):  # Windows 上 SIGTERM 支持有限,有就注册
        signal.signal(signal.SIGTERM, request_stop)

    # 启动时把关键配置打出来:上传目录对不上(两个服务指向不同目录)是最常见的部署问题
    log.info("消费者启动: topic=%s group=%s dlq=%s upload_dir=%s",
             settings.doc_upload_topic, settings.doc_consumer_group,
             settings.doc_upload_dlq_topic, Path(settings.upload_dir).resolve())
    worker.run(stop)
    log.info("消费者已退出")


if __name__ == "__main__":
    main()
