"""Tests for the consume loop. Fake Consumer / Producer / sleep throughout: no real Kafka and no
real backoff waits, yet every branch runs (success, bad message, retry, dead-letter, failed
dead-letter write).

What they check is ORDER AND GUARANTEES:
  - success: the offset is committed only after processing (the core of "at least once")
  - bad message: committed only after it is in the dead-letter topic, without blocking later messages
  - dead-letter write fails: never commit; crash the process so the message is not lost
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
VALID = json.dumps(CONTRACT[0], ensure_ascii=False).encode("utf-8")  # a valid sop event


# ------------------------------------------------------------------ fakes


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
            self._on_empty()  # out of messages: stop the run() loop in the test
        return None

    def commit(self, message=None, asynchronous=True):
        assert asynchronous is False, "commits must be synchronous, or after a crash nobody knows whether the commit took effect"
        self.calls.append(("commit", message.offset()))

    def close(self): self.calls.append(("close",))


class FakeProducer:
    """mode: deliver = delivered / error = the callback reports a failure / queued = flush timed out with messages unsent"""

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
            cb(RuntimeError("broker unavailable") if self.mode == "error" else None, None)
        self._callbacks.clear()
        return 0


# ------------------------------------------------------------------ wiring


@pytest.fixture
def calls():
    return []  # one call log shared by all fakes, to assert what happened before what


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


def recording_handler(calls, fail_times=0, error=RuntimeError("temporary failure")):
    state = {"n": 0}

    def handler(event):
        state["n"] += 1
        calls.append(("handler", event.upload_id))
        if state["n"] <= fail_times:
            raise error
    handler.state = state
    return handler


# ------------------------------------------------------------------ success path


def test_success_commits_only_after_the_handler_finished(calls):
    worker, producer, _ = make(calls, [FakeMessage(VALID, offset=5)], recording_handler(calls))

    assert worker.poll_once(0) is True

    # The order is the guarantee: process, then commit (at least once). Reversed it is "at most once" and a crash loses the message
    assert calls == [("handler", CONTRACT[0]["uploadId"]), ("commit", 5)]
    assert producer.sent == []


def test_empty_poll_does_nothing(calls):
    worker, _, _ = make(calls, [], recording_handler(calls))
    assert worker.poll_once(0) is False
    assert calls == []


# ------------------------------------------------------------------ bad message -> dead-letter topic


@pytest.mark.parametrize("raw", [
    pytest.param(b"this is not json", id="not JSON"),
    pytest.param(b"\xff\xfe\x00garbage", id="invalid UTF-8"),
    pytest.param(b"", id="empty body"),
    pytest.param(b'["a", "list"]', id="JSON but not an object"),
    pytest.param(b'{"uploadId": "u-1"}', id="required fields missing"),
    pytest.param(json.dumps({**CONTRACT[0], "docType": "SOP"}).encode(), id="enum value in the wrong case"),
])
def test_malformed_messages_go_to_the_dlq_without_calling_the_handler(calls, raw):
    handler = recording_handler(calls)
    worker, producer, sleeps = make(calls, [FakeMessage(raw, offset=9)], handler)

    worker.poll_once(0)

    assert handler.state["n"] == 0                    # a bad message never reaches the handler
    assert sleeps == []                               # and is never retried; it stays broken however often we retry
    assert calls == [("dlq", "document-upload-events.dlq"), ("commit", 9)]  # dead-letter first, commit after
    sent = producer.sent[0]
    assert sent["value"] == raw                       # the original bytes, not re-serialized
    assert sent["headers"]["x-attempts"] == "0"
    assert sent["headers"]["x-source-offset"] == "9"
    assert sent["headers"]["x-error"]                 # with the failure reason, for whoever investigates later


def test_a_poison_message_does_not_block_the_messages_behind_it(calls):
    handler = recording_handler(calls)
    worker, producer, _ = make(
        calls, [FakeMessage(b"garbage", offset=1), FakeMessage(VALID, offset=2)], handler)

    worker.poll_once(0)
    worker.poll_once(0)

    assert handler.state["n"] == 1                    # the second message was processed normally
    assert [c for c in calls if c[0] == "commit"] == [("commit", 1), ("commit", 2)]
    assert len(producer.sent) == 1


# ------------------------------------------------------------------ retries


def test_transient_failures_are_retried_with_exponential_backoff_then_succeed(calls):
    handler = recording_handler(calls, fail_times=2)
    worker, producer, sleeps = make(calls, [FakeMessage(VALID, offset=3)], handler, backoff_seconds=0.5)

    worker.poll_once(0)

    assert handler.state["n"] == 3
    assert sleeps == [0.5, 1.0]                       # 0.5 * 2^0, 0.5 * 2^1
    assert calls[-1] == ("commit", 3) and calls.count(("commit", 3)) == 1  # committed once, only after the final success
    assert producer.sent == []


def test_retries_exhausted_sends_to_the_dlq_and_records_the_last_error(calls):
    handler = recording_handler(calls, fail_times=99, error=ConnectionError("downstream timeout"))
    worker, producer, sleeps = make(calls, [FakeMessage(VALID, offset=4)], handler, max_attempts=3)

    worker.poll_once(0)

    assert handler.state["n"] == 3
    assert len(sleeps) == 2                           # no pointless wait after the last failure
    sent = producer.sent[0]
    assert sent["value"] == VALID
    assert sent["headers"]["x-attempts"] == "3"
    assert "ConnectionError" in sent["headers"]["x-error"] and "downstream timeout" in sent["headers"]["x-error"]
    assert calls[-2:] == [("dlq", "document-upload-events.dlq"), ("commit", 4)]


def test_a_permanent_error_from_the_handler_skips_retries(calls):
    handler = recording_handler(calls, fail_times=99, error=PermanentError("path outside the upload folder"))
    worker, producer, sleeps = make(calls, [FakeMessage(VALID, offset=6)], handler, max_attempts=3)

    worker.poll_once(0)

    assert handler.state["n"] == 1 and sleeps == []   # known to be hopeless, so no retry
    assert producer.sent[0]["headers"]["x-attempts"] == "1"


# ------------------------------------------------------------------ recording the failure (on_failure)


def recording_failures(calls):
    def on_failure(event, reason):
        calls.append(("failed", event.upload_id, reason))
    return on_failure


@pytest.mark.parametrize("error, attempts", [
    (PermanentError("cannot parse the file"), 1),
    (ConnectionError("downstream timeout"), 3),
], ids=["permanent", "retries exhausted"])
def test_a_dead_lettered_upload_is_recorded_as_failed_after_the_dlq_write(calls, error, attempts):
    handler = recording_handler(calls, fail_times=99, error=error)
    worker, _, _ = make(calls, [FakeMessage(VALID, offset=8)], handler, on_failure=recording_failures(calls))

    worker.poll_once(0)

    upload_id = CONTRACT[0]["uploadId"]
    # order: the dead letter is safe first, then the status is recorded, then the offset is committed
    assert calls[-3:] == [("dlq", "document-upload-events.dlq"), ("failed", upload_id, calls[-2][2]), ("commit", 8)]
    assert str(error) in calls[-2][2]


def test_an_unparseable_message_has_no_upload_to_record(calls):
    worker, _, _ = make(calls, [FakeMessage(b"not json", offset=2)], recording_handler(calls),
                        on_failure=recording_failures(calls))

    worker.poll_once(0)

    assert not [c for c in calls if c[0] == "failed"]
    assert calls[-1] == ("commit", 2)


def test_a_success_records_no_failure(calls):
    worker, _, _ = make(calls, [FakeMessage(VALID, offset=3)], recording_handler(calls),
                        on_failure=recording_failures(calls))

    worker.poll_once(0)

    assert not [c for c in calls if c[0] == "failed"]


def test_if_recording_the_failure_fails_the_offset_is_still_committed(calls):
    # The dead letter already holds the message; a database outage while recording the status must not
    # make the consumer retry a message that is already safe
    def broken_on_failure(event, reason):
        raise RuntimeError("database down")
    handler = recording_handler(calls, fail_times=99, error=PermanentError("bad file"))
    worker, _, _ = make(calls, [FakeMessage(VALID, offset=5)], handler, on_failure=broken_on_failure)

    worker.poll_once(0)

    assert calls[-2:] == [("dlq", "document-upload-events.dlq"), ("commit", 5)]


# ------------------------------------------------------------------ dead-letter write fails: never commit


@pytest.mark.parametrize("mode", ["error", "queued"])
def test_if_the_dlq_write_fails_the_offset_is_never_committed(calls, mode):
    """Committing the offset after a failed dead-letter write makes the message vanish. Crash rather than lose it."""
    worker, _, _ = make(calls, [FakeMessage(b"garbage", offset=7)], recording_handler(calls), producer_mode=mode)

    with pytest.raises(RuntimeError, match="dead-letter write failed"):
        worker.poll_once(0)

    assert ("commit", 7) not in calls


# ------------------------------------------------------------------ Kafka-level errors


def test_non_fatal_kafka_errors_are_logged_and_skipped(calls):
    handler = recording_handler(calls)
    worker, _, sleeps = make(calls, [FakeMessage(None, error=FakeError(fatal=False))], handler)

    assert worker.poll_once(0) is False

    assert handler.state["n"] == 0 and calls == []    # no processing, no commit
    assert sleeps == [1]                              # wait a moment instead of spinning and flooding the log


def test_fatal_kafka_errors_raise(calls):
    worker, _, _ = make(calls, [FakeMessage(None, error=FakeError(fatal=True))], recording_handler(calls))
    with pytest.raises(KafkaException):
        worker.poll_once(0)


# ------------------------------------------------------------------ lifecycle


def test_run_subscribes_stops_on_the_event_and_always_closes_the_consumer(calls):
    stop = threading.Event()
    consumer = FakeConsumer([FakeMessage(VALID, offset=1)], calls, on_empty=stop.set)
    worker = UploadConsumer(
        consumer, FakeProducer(calls), recording_handler(calls),
        topic="document-upload-events", dlq_topic="x.dlq", sleep=lambda _s: None)

    worker.run(stop)

    assert calls[0] == ("subscribe", ("document-upload-events",))
    assert ("commit", 1) in calls
    assert calls[-1] == ("close",)                    # always close on exit, leaving the consumer group cleanly


def test_the_consumer_is_closed_even_if_processing_crashes(calls):
    stop = threading.Event()
    consumer = FakeConsumer([FakeMessage(b"garbage", offset=1)], calls, on_empty=stop.set)
    worker = UploadConsumer(
        consumer, FakeProducer(calls, "error"), recording_handler(calls),
        topic="t", dlq_topic="t.dlq", sleep=lambda _s: None)

    with pytest.raises(RuntimeError):
        worker.run(stop)

    assert calls[-1] == ("close",)


# ------------------------------------------------------------------ other


def test_parse_event_tolerates_a_utf8_bom():
    # Seen for real: messages piped through PowerShell start with a BOM
    assert parse_event(b"\xef\xbb\xbf" + VALID).upload_id == CONTRACT[0]["uploadId"]


def test_the_dlq_topic_name_is_derived_from_the_main_topic():
    assert Settings(doc_upload_topic="foo").doc_upload_dlq_topic == "foo.dlq"
