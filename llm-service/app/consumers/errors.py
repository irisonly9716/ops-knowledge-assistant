"""Two kinds of failure in the consumer; which one it is decides "retry" vs "straight to the dead-letter topic"."""


class PermanentError(Exception):
    """The input itself is broken and retrying will never help: the message is not valid
    JSON, fields break the contract, storage_key tries to escape the upload folder...

    It goes to the dead-letter queue at once: no wasted retries, and it must not block the
    messages behind it.

    Every other exception (file temporarily unreadable, downstream timeout, network blip...)
    is treated as possibly temporary and retried with exponential backoff; only when the
    retries run out does the message go to the dead-letter topic.
    Defaulting to "temporary" and marking only known-hopeless cases as permanent is deliberate:
    treating a temporary error as permanent sends a message that could have succeeded to the
    dead-letter topic, while the opposite mistake only costs a few extra retries.
    """
