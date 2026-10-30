"""Turn the storage_key in a message into the real file path on this machine."""
from pathlib import Path

from app.config import settings
from app.consumers.errors import PermanentError


def resolve_upload_path(storage_key: str, upload_dir: str | Path | None = None) -> Path:
    """storage_key -> the real file inside the upload folder.

    The message comes from Kafka. It is an internal channel, but its content still CANNOT be
    trusted: if storage_key is "../../etc/passwd" or an absolute path, joining it onto the
    folder and reading the result is path traversal. So after joining, check that the result
    is still inside the upload folder.

    resolve() first (expanding .. and symlinks into the real path), then compare. Checking
    storage_key for ".." is not enough: symlinks, absolute paths and case/encoding tricks all
    get past a string check.

    The two failures are different in nature, so they raise different exceptions (see errors.py):
      outside the folder -> PermanentError: the message is broken forever, retrying is pointless
      file not found     -> FileNotFoundError (an ordinary exception): may be temporary, e.g. a
                            shared volume not synced yet; the caller retries with exponential
                            backoff and only then sends it to the dead-letter topic
    """
    base = Path(upload_dir if upload_dir is not None else settings.upload_dir).resolve()
    candidate = (base / storage_key).resolve()  # careful: if storage_key is absolute, `/` drops base entirely

    if candidate == base or not candidate.is_relative_to(base):
        raise PermanentError(f"storage_key points outside the upload folder, rejected: {storage_key!r}")
    if not candidate.is_file():
        raise FileNotFoundError(f"file not found in the upload folder: {storage_key!r} (folder: {base})")
    return candidate
