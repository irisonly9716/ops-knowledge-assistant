"""storage_key -> real file path. The focus is the path traversal defense."""
import os

import pytest

from app.consumers.errors import PermanentError
from app.consumers.storage import resolve_upload_path


@pytest.fixture
def upload_dir(tmp_path):
    root = tmp_path / "uploads"
    root.mkdir()
    (root / "ok.md").write_text("content", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("must never be read", encoding="utf-8")  # OUTSIDE the upload folder
    return root


def test_resolves_a_normal_key(upload_dir):
    assert resolve_upload_path("ok.md", upload_dir) == (upload_dir / "ok.md").resolve()


@pytest.mark.parametrize("evil", [
    "../secret.txt",                 # the classic climb out
    "sub/../../secret.txt",          # into a subfolder, then up: beats a naive "doesn't start with .." check
    "..\\secret.txt",                # Windows style
    "/etc/passwd",                   # absolute path: pathlib's `/` silently drops the base in front
    "C:\\Windows\\win.ini",
    ".",                             # the upload folder itself, not a file
    "",
])
def test_paths_that_escape_the_upload_dir_are_permanent_errors(upload_dir, evil):
    with pytest.raises(PermanentError):
        resolve_upload_path(evil, upload_dir)


def test_missing_file_is_a_retryable_error_not_a_permanent_one(upload_dir):
    # A missing file may just be a shared volume not synced yet, so it is an ordinary (retried) exception, not a PermanentError
    with pytest.raises(FileNotFoundError):
        resolve_upload_path("not-there.md", upload_dir)
    assert not issubclass(FileNotFoundError, PermanentError)


@pytest.mark.skipif(os.name == "nt", reason="creating symlinks on Windows needs administrator rights")
def test_a_symlink_pointing_outside_is_rejected(upload_dir, tmp_path):
    # A string check (does the key contain "..") can't stop this; resolve() to the real path first, then compare
    (upload_dir / "link.md").symlink_to(tmp_path / "secret.txt")
    with pytest.raises(PermanentError):
        resolve_upload_path("link.md", upload_dir)
