"""Consumer of document-upload-events (a separate worker process).

Run:   cd llm-service
       uv run python -m app.consumers.upload_consumer
Ctrl+C shuts down gracefully: it finishes the message in hand, leaves the consumer group
cleanly, then exits.

Why a separate process instead of a background thread inside FastAPI:
  - The consumer is a long-running, blocking poll loop; the HTTP side is an async event loop.
    Two different kinds of work in one process means carefully keeping threads and the event
    loop out of each other's way
  - Independent lifecycles: restarting or scaling the web service must not interrupt a document
    being processed, and the other way round
  - Independent scaling: more workers in the same consumer group share the partitions; in Docker
    it is one image with two start commands (uvicorn / this module)
  - A plain blocking loop only needs the synchronous confluent-kafka client, no aiokafka
    (bottom line: separate process + synchronous client is the simplest and most robust)

Delivery semantics: AT LEAST ONCE. Auto-commit is off; the offset is committed manually only
after the message is processed (or safely in the dead-letter topic).
  processed but crashed before committing -> redelivered after restart -> processing must
  tolerate duplicates (see handlers.py)
  The opposite order (commit, then process) is "at most once": a crash loses the message,
  which is unacceptable for document ingestion.
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
from app.consumers.handlers import UploadHandler
from app.rag.embeddings import openai_embedder_from_settings

log = logging.getLogger("upload_consumer")

Handler = Callable[[UploadEvent], None]
# Called with the event and the reason once a message has been dead-lettered (to record the failure)
FailureCallback = Callable[[UploadEvent, str], None]


def parse_event(raw: bytes | None) -> UploadEvent:
    """Raw bytes -> UploadEvent. Any failure is a PermanentError (the message itself is broken)."""
    if raw is None:
        raise PermanentError("empty message body")
    try:
        # json.loads accepts bytes and handles a UTF-8 BOM. Messages piped through PowerShell
        # have been seen to start with one, so this tolerates them for free
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise PermanentError(f"message is not valid UTF-8 JSON: {e}") from e
    try:
        return UploadEvent.model_validate(payload)
    except ValidationError as e:
        problems = "; ".join(f"{'.'.join(map(str, err['loc'])) or '(message)'}: {err['msg']}" for err in e.errors())
        raise PermanentError(f"message breaks the contract: {problems}") from e


class UploadConsumer:
    """The consume loop. consumer / dlq_producer / handler / on_failure / sleep are all passed in
    (dependency injection), so tests can swap in fakes and cover every branch without a real Kafka
    and without actually waiting out the backoff."""

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
        on_failure: FailureCallback | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._consumer = consumer
        self._dlq = dlq_producer
        self._handler = handler
        self._topic = topic
        self._dlq_topic = dlq_topic
        self._max_attempts = max_attempts
        self._backoff = backoff_seconds
        self._on_failure = on_failure
        self._sleep = sleep

    # ------------------------------------------------------------------ main loop

    def run(self, stop: threading.Event, poll_timeout: float = 1.0) -> None:
        self._consumer.subscribe([self._topic])
        try:
            while not stop.is_set():
                self.poll_once(poll_timeout)  # poll has a timeout, so it exits within a second of stop being set
        finally:
            self._consumer.close()  # leave the group cleanly so another member takes over at once, without waiting for the session timeout

    def poll_once(self, timeout: float) -> bool:
        """Fetch one message and process it. Returns whether a message was actually processed."""
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
        # Non-fatal errors (typically: topic not created yet -> UNKNOWN_TOPIC_OR_PART; broker
        # temporarily unreachable). The client reconnects by itself; just log and wait a moment
        # instead of spinning and flooding the log
        log.warning("Kafka reported a non-fatal error, continuing shortly: %s", err)
        self._sleep(1)

    # ------------------------------------------------------------------ one message

    def _process(self, msg) -> None:
        """Every message ends in exactly one of two places, "succeeded" or "dead-lettered",
        and only then is its offset committed."""
        try:
            event = parse_event(msg.value())
        except PermanentError as e:
            log.error("invalid message, dead-lettering (partition=%s offset=%s): %s", msg.partition(), msg.offset(), e)
            self._dead_letter(msg, str(e), attempts=0)
            return

        last_error = "unknown error"
        for attempt in range(1, self._max_attempts + 1):
            try:
                self._handler(event)
            except PermanentError as e:
                log.error("upload_id=%s failed permanently, dead-lettering: %s", event.upload_id, e)
                self._dead_letter(msg, str(e), attempts=attempt, event=event)
                return
            except Exception as e:  # noqa: BLE001 -- catching ANY failure is the point; PermanentError above does the sorting
                last_error = f"{type(e).__name__}: {e}"
                if attempt < self._max_attempts:
                    wait = self._backoff * 2 ** (attempt - 1)
                    log.warning("upload_id=%s attempt %d/%d failed, retrying in %.1f s: %s",
                                event.upload_id, attempt, self._max_attempts, wait, last_error)
                    self._sleep(wait)
                continue
            self._commit(msg)  # process successfully FIRST, commit AFTER: at least once
            return

        log.error("upload_id=%s still failing after %d attempts, dead-lettering: %s",
                  event.upload_id, self._max_attempts, last_error)
        self._dead_letter(msg, last_error, attempts=self._max_attempts, event=event)

    def _dead_letter(self, msg, reason: str, attempts: int, event: UploadEvent | None = None) -> None:
        """Forward the original message (the raw bytes, not re-serialized) with the failure reason
        to the dead-letter topic, then commit the offset.

        Why a dead-letter topic: a bad message must neither be retried forever and block the
        messages behind it (head-of-line blocking) nor disappear silently. It is set aside and
        kept, the main flow continues, and a person can inspect, fix and replay it later.

        Iron rule: commit the offset only after the dead-letter write is CONFIRMED. Committing
        after a failed dead-letter write makes the message vanish. So a failed write raises and
        crashes the process: after restart the offset is uncommitted and the message is
        redelivered. Better to stop than to lose data.

        Once the dead letter is safely written, on_failure records the failure (the upload's status
        becomes failed). That is best effort: if it fails, the message is still safe in the dead-letter
        topic, so the error is logged and the offset committed anyway.
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
            raise RuntimeError(f"dead-letter write failed ({still_queued} undelivered, errors {errors}); "
                               "not committing the offset so the message is not lost")
        if event is not None and self._on_failure is not None:
            try:
                self._on_failure(event, reason)
            except Exception:  # noqa: BLE001 -- the dead letter is the safety net; recording the status is a bonus
                log.exception("upload_id=%s: could not record the failure", event.upload_id)
        self._commit(msg)

    def _commit(self, msg) -> None:
        # Commit this one message synchronously: asynchronous=False waits for the broker to
        # confirm. What gets committed is msg.offset()+1 ("where to read next"), not msg.offset()
        self._consumer.commit(message=msg, asynchronous=False)


# ---------------------------------------------------------------------- entry point


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    consumer = Consumer({
        "bootstrap.servers": settings.kafka_bootstrap_servers,
        "group.id": settings.doc_consumer_group,
        # A new consumer group starts from the earliest message the first time: files uploaded
        # before the consumer started must be processed too. (A group with committed offsets
        # always continues from its last commit.)
        "auto.offset.reset": "earliest",
        # Auto-commit off: auto-commit marks everything polled as processed every few seconds,
        # regardless of real progress, so messages can be marked done before they are handled.
        "enable.auto.commit": False,
    })
    producer = Producer({"bootstrap.servers": settings.kafka_bootstrap_servers})
    # Fails right here if OPENAI_API_KEY is missing: better at startup than on the first message
    handler = UploadHandler(openai_embedder_from_settings())

    worker = UploadConsumer(
        consumer, producer, handler, on_failure=handler.mark_failed,
        topic=settings.doc_upload_topic,
        dlq_topic=settings.doc_upload_dlq_topic,
        max_attempts=settings.doc_consumer_max_attempts,
        backoff_seconds=settings.doc_consumer_backoff_seconds,
    )

    stop = threading.Event()

    def request_stop(signum, _frame) -> None:
        log.info("received stop signal (%s); exiting after the current message", signum)
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    if hasattr(signal, "SIGTERM"):  # SIGTERM support on Windows is limited; register it where it exists
        signal.signal(signal.SIGTERM, request_stop)

    # Print the key settings at startup: mismatched upload folders (the two services pointing at
    # different directories) is the most common deployment problem
    log.info("consumer starting: topic=%s group=%s dlq=%s upload_dir=%s",
             settings.doc_upload_topic, settings.doc_consumer_group,
             settings.doc_upload_dlq_topic, Path(settings.upload_dir).resolve())
    worker.run(stop)
    log.info("consumer stopped")


if __name__ == "__main__":
    main()
