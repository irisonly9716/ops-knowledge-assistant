"""storage_key -> 真实文件路径。重点是路径穿越防御。"""
import os

import pytest

from app.consumers.errors import PermanentError
from app.consumers.storage import resolve_upload_path


@pytest.fixture
def upload_dir(tmp_path):
    root = tmp_path / "uploads"
    root.mkdir()
    (root / "ok.md").write_text("内容", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("不该被读到", encoding="utf-8")  # 在上传目录【外面】
    return root


def test_resolves_a_normal_key(upload_dir):
    assert resolve_upload_path("ok.md", upload_dir) == (upload_dir / "ok.md").resolve()


@pytest.mark.parametrize("evil", [
    "../secret.txt",                 # 最经典的向上穿越
    "sub/../../secret.txt",          # 先进子目录再向上,绕过"开头不是 .."的简单检查
    "..\\secret.txt",                # Windows 风格
    "/etc/passwd",                   # 绝对路径:pathlib 的 `/` 会直接丢掉前面的 base
    "C:\\Windows\\win.ini",
    ".",                             # 上传目录本身,不是文件
    "",
])
def test_paths_that_escape_the_upload_dir_are_permanent_errors(upload_dir, evil):
    with pytest.raises(PermanentError):
        resolve_upload_path(evil, upload_dir)


def test_missing_file_is_a_retryable_error_not_a_permanent_one(upload_dir):
    # 文件不存在可能只是共享卷还没同步好,所以是普通异常(会被重试),不是 PermanentError
    with pytest.raises(FileNotFoundError):
        resolve_upload_path("not-there.md", upload_dir)
    assert not issubclass(FileNotFoundError, PermanentError)


@pytest.mark.skipif(os.name == "nt", reason="Windows 上创建符号链接需要管理员权限")
def test_a_symlink_pointing_outside_is_rejected(upload_dir, tmp_path):
    # 字符串检查(key 里有没有 "..")拦不住它,必须先 resolve() 展开真实路径再比较
    (upload_dir / "link.md").symlink_to(tmp_path / "secret.txt")
    with pytest.raises(PermanentError):
        resolve_upload_path("link.md", upload_dir)
