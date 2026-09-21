"""把消息里的 storage_key 翻译成本机上真实的文件路径。"""
from pathlib import Path

from app.config import settings
from app.consumers.errors import PermanentError


def resolve_upload_path(storage_key: str, upload_dir: str | Path | None = None) -> Path:
    """storage_key -> 上传目录里的真实文件。

    消息来自 Kafka,虽然是内部通道,也【不能信任它的内容】:如果 storage_key 是
    "../../etc/passwd" 或者一个绝对路径,直接拼路径去读,就是路径穿越(path traversal)。
    所以拼完之后必须确认结果仍然落在上传目录内部。

    做法是先 resolve()(把 .. 、符号链接全部展开成真实路径)再比较——不能只检查
    storage_key 里有没有 ".." 字样:符号链接、绝对路径、大小写/编码花样都能绕过字符串检查。

    两种失败的性质不同,所以抛不同的异常(见 errors.py):
      越界      -> PermanentError:这条消息永远是坏的,重试没意义
      文件不存在 -> FileNotFoundError(普通异常):可能只是共享卷还没同步好之类的暂时问题,
                   让上层按指数退避重试几次,仍不行再进死信
    """
    base = Path(upload_dir if upload_dir is not None else settings.upload_dir).resolve()
    candidate = (base / storage_key).resolve()  # 注意:storage_key 若是绝对路径,`/` 会直接丢掉 base

    if candidate == base or not candidate.is_relative_to(base):
        raise PermanentError(f"storage_key 越出了上传目录,拒绝: {storage_key!r}")
    if not candidate.is_file():
        raise FileNotFoundError(f"上传目录里找不到文件: {storage_key!r}(目录: {base})")
    return candidate
