"""消费循环的测试。全部用假的 Consumer / Producer / sleep——不连真 Kafka,也不真等退避时间,
就能把每个分支(成功、坏消息、重试、死信、死信也失败)都跑到。

重点验证的是【顺序和保证】:
  - 成功:处理完之后才提交 offset("至少一次"的核心)
  - 坏消息:进死信之后才提交,并且不阻塞后面的消息
  - 死信写不进去:绝对不提交,让进程崩溃,消息不丢
"""
import json
import threading
from pathlib import Path

import pytest
from confluent_kafka import KafkaException

from app.config import Settings
from app.consumers.errors import PermanentError
from app.consumers.upload_consumer import UploadConsumer, parse_event

CONTRACT = json.loads(
    (Path(__file__).resolve().parents[2] / "contracts" / "upload-event.examples.json").read_text(encoding="utf-8"))
VALID = json.dumps(CONTRACT[0], ensure_ascii=False).encode("utf-8")  # 一条合法的 sop 事件


# ------------------------------------------------------------------ 假对象


class FakeError:
    def __init__(self, fatal=False):
        self._fatal = fatal

    def fatal(self):
        return self._fatal

    def __str__(self):
        return "FakeError(fatal)" if self._fatal else "FakeError(UNKNOWN_TOPIC_OR_PART)"


class FakeMessage:
    def __init__(self, value, offset=0, error=None):
        self._value, self._offset, self._error = value, offset, error

    def value(self): return self._value
    def key(self): return b"key-" + str(self._offset).encode()
    def topic(self): return "document-upload-events"
    def partition(self): return 0
    def offset(self): return self._offset
    def error(self): return self._error


class FakeConsumer:
    def __init__(self, messages, calls, on_empty=None):
        self._messages, self.calls, self._on_empty = list(messages), calls, on_empty

    def subscribe(self, topics): self.calls.append(("subscribe", tuple(topics)))

    def poll(self, timeout):
        if self._messages:
            return self._messages.pop(0)
        if self._on_empty:
            self._on_empty()  # 消息用完了:让测试里的 run() 循环停下来
        return None

    def commit(self, message=None, asynchronous=True):
        assert asynchronous is False, "必须同步提交,否则崩溃时无法确定提交有没有生效"
        self.calls.append(("commit", message.offset()))

    def close(self): self.calls.append(("close",))


class FakeProducer:
    """mode: deliver=正常送达 / error=回调报告失败 / queued=flush 超时仍有消息没发出去"""

    def __init__(self, calls, mode="deliver"):
        self.calls, self.mode, self.sent, self._callbacks = calls, mode, [], []

    def produce(self, topic, key=None, value=None, headers=None, callback=None):
        self.sent.append({"topic": topic, "key": key, "value": value,
                          "headers": {k: v.decode() for k, v in headers}})
        self._callbacks.append(callback)
        self.calls.append(("dlq", topic))

    def flush(self, timeout):
        if self.mode == "queued":
            return 1
        for cb in self._callbacks:
            cb(RuntimeError("broker 不可用") if self.mode == "error" else None, None)
        self._callbacks.clear()
        return 0


# ------------------------------------------------------------------ 组装


@pytest.fixture
def calls():
    return []  # 所有假对象共用的调用记录,用来断言"谁先谁后"


def make(calls, messages, handler, producer_mode="deliver", **kw):
    sleeps = []
    producer = FakeProducer(calls, producer_mode)
    consumer = FakeConsumer(messages, calls)
    worker = UploadConsumer(
        consumer, producer, handler,
        topic="document-upload-events", dlq_topic="document-upload-events.dlq",
        max_attempts=kw.pop("max_attempts", 3), backoff_seconds=kw.pop("backoff_seconds", 0.5),
        sleep=sleeps.append, **kw)
    return worker, producer, sleeps


def recording_handler(calls, fail_times=0, error=RuntimeError("暂时的故障")):
    state = {"n": 0}

    def handler(event):
        state["n"] += 1
        calls.append(("handler", event.upload_id))
        if state["n"] <= fail_times:
            raise error
    handler.state = state
    return handler


# ------------------------------------------------------------------ 成功路径


def test_success_commits_only_after_the_handler_finished(calls):
    worker, producer, _ = make(calls, [FakeMessage(VALID, offset=5)], recording_handler(calls))

    assert worker.poll_once(0) is True

    # 顺序就是保证:先处理,后提交(至少一次)。顺序反过来就是"至多一次",崩溃会丢消息
    assert calls == [("handler", CONTRACT[0]["uploadId"]), ("commit", 5)]
    assert producer.sent == []


def test_empty_poll_does_nothing(calls):
    worker, _, _ = make(calls, [], recording_handler(calls))
    assert worker.poll_once(0) is False
    assert calls == []


# ------------------------------------------------------------------ 坏消息 -> 死信


@pytest.mark.parametrize("raw", [
    pytest.param(b"this is not json", id="不是 JSON"),
    pytest.param(b"\xff\xfe\x00garbage", id="不是合法 UTF-8"),
    pytest.param(b"", id="空消息体"),
    pytest.param(b'["a", "list"]', id="JSON 但不是对象"),
    pytest.param(b'{"uploadId": "u-1"}', id="缺必填字段"),
    pytest.param(json.dumps({**CONTRACT[0], "docType": "SOP"}).encode(), id="枚举值大小写不对"),
])
def test_malformed_messages_go_to_the_dlq_without_calling_the_handler(calls, raw):
    handler = recording_handler(calls)
    worker, producer, sleeps = make(calls, [FakeMessage(raw, offset=9)], handler)

    worker.poll_once(0)

    assert handler.state["n"] == 0                    # 坏消息根本不该进业务处理
    assert sleeps == []                               # 也不该重试——重试一万次它还是坏的
    assert calls == [("dlq", "document-upload-events.dlq"), ("commit", 9)]  # 先进死信,后提交
    sent = producer.sent[0]
    assert sent["value"] == raw                       # 原样字节转存,不重新序列化
    assert sent["headers"]["x-attempts"] == "0"
    assert sent["headers"]["x-source-offset"] == "9"
    assert sent["headers"]["x-error"]                 # 带着失败原因,方便事后排查


def test_a_poison_message_does_not_block_the_messages_behind_it(calls):
    handler = recording_handler(calls)
    worker, producer, _ = make(
        calls, [FakeMessage(b"garbage", offset=1), FakeMessage(VALID, offset=2)], handler)

    worker.poll_once(0)
    worker.poll_once(0)

    assert handler.state["n"] == 1                    # 第二条正常处理了
    assert [c for c in calls if c[0] == "commit"] == [("commit", 1), ("commit", 2)]
    assert len(producer.sent) == 1


# ------------------------------------------------------------------ 重试


def test_transient_failures_are_retried_with_exponential_backoff_then_succeed(calls):
    handler = recording_handler(calls, fail_times=2)
    worker, producer, sleeps = make(calls, [FakeMessage(VALID, offset=3)], handler, backoff_seconds=0.5)

    worker.poll_once(0)

    assert handler.state["n"] == 3
    assert sleeps == [0.5, 1.0]                       # 0.5 * 2^0, 0.5 * 2^1
    assert calls[-1] == ("commit", 3) and calls.count(("commit", 3)) == 1  # 只在最终成功后提交一次
    assert producer.sent == []


def test_retries_exhausted_sends_to_the_dlq_and_records_the_last_error(calls):
    handler = recording_handler(calls, fail_times=99, error=ConnectionError("下游超时"))
    worker, producer, sleeps = make(calls, [FakeMessage(VALID, offset=4)], handler, max_attempts=3)

    worker.poll_once(0)

    assert handler.state["n"] == 3
    assert len(sleeps) == 2                           # 最后一次失败后不再白等
    sent = producer.sent[0]
    assert sent["value"] == VALID
    assert sent["headers"]["x-attempts"] == "3"
    assert "ConnectionError" in sent["headers"]["x-error"] and "下游超时" in sent["headers"]["x-error"]
    assert calls[-2:] == [("dlq", "document-upload-events.dlq"), ("commit", 4)]


def test_a_permanent_error_from_the_handler_skips_retries(calls):
    handler = recording_handler(calls, fail_times=99, error=PermanentError("路径越界"))
    worker, producer, sleeps = make(calls, [FakeMessage(VALID, offset=6)], handler, max_attempts=3)

    worker.poll_once(0)

    assert handler.state["n"] == 1 and sleeps == []   # 已知无解,不重试
    assert producer.sent[0]["headers"]["x-attempts"] == "1"


# ------------------------------------------------------------------ 死信也失败:绝不提交


@pytest.mark.parametrize("mode", ["error", "queued"])
def test_if_the_dlq_write_fails_the_offset_is_never_committed(calls, mode):
    """如果死信没写成功就提交了 offset,这条消息就凭空消失了。宁可崩溃也不能丢。"""
    worker, _, _ = make(calls, [FakeMessage(b"garbage", offset=7)], recording_handler(calls), producer_mode=mode)

    with pytest.raises(RuntimeError, match="死信写入失败"):
        worker.poll_once(0)

    assert ("commit", 7) not in calls


# ------------------------------------------------------------------ Kafka 层错误


def test_non_fatal_kafka_errors_are_logged_and_skipped(calls):
    handler = recording_handler(calls)
    worker, _, sleeps = make(calls, [FakeMessage(None, error=FakeError(fatal=False))], handler)

    assert worker.poll_once(0) is False

    assert handler.state["n"] == 0 and calls == []    # 不处理、不提交
    assert sleeps == [1]                              # 稍等一下,避免空转刷屏


def test_fatal_kafka_errors_raise(calls):
    worker, _, _ = make(calls, [FakeMessage(None, error=FakeError(fatal=True))], recording_handler(calls))
    with pytest.raises(KafkaException):
        worker.poll_once(0)


# ------------------------------------------------------------------ 生命周期


def test_run_subscribes_stops_on_the_event_and_always_closes_the_consumer(calls):
    stop = threading.Event()
    consumer = FakeConsumer([FakeMessage(VALID, offset=1)], calls, on_empty=stop.set)
    worker = UploadConsumer(
        consumer, FakeProducer(calls), recording_handler(calls),
        topic="document-upload-events", dlq_topic="x.dlq", sleep=lambda _s: None)

    worker.run(stop)

    assert calls[0] == ("subscribe", ("document-upload-events",))
    assert ("commit", 1) in calls
    assert calls[-1] == ("close",)                    # 退出时一定 close,正常离开消费组


def test_the_consumer_is_closed_even_if_processing_crashes(calls):
    stop = threading.Event()
    consumer = FakeConsumer([FakeMessage(b"garbage", offset=1)], calls, on_empty=stop.set)
    worker = UploadConsumer(
        consumer, FakeProducer(calls, "error"), recording_handler(calls),
        topic="t", dlq_topic="t.dlq", sleep=lambda _s: None)

    with pytest.raises(RuntimeError):
        worker.run(stop)

    assert calls[-1] == ("close",)


# ------------------------------------------------------------------ 其它


def test_parse_event_tolerates_a_utf8_bom():
    # 实际遇到过:PowerShell 管道发出来的消息开头会带 BOM
    assert parse_event(b"\xef\xbb\xbf" + VALID).upload_id == CONTRACT[0]["uploadId"]


def test_the_dlq_topic_name_is_derived_from_the_main_topic():
    assert Settings(doc_upload_topic="foo").doc_upload_dlq_topic == "foo.dlq"
