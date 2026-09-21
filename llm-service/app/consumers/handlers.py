"""收到一条合法的上传事件之后,具体干什么。

Day 8 只做最小版:确认"上传 -> Kafka -> 消费者收到 -> 读得到文件"这条链路是通的。
后面每天往这里加东西(按 event.content_kind 分流):
  Day 9   解析 + chunking
  Day 10  embedding
  Day 11  写入 pgvector
  Day 12  更新处理状态;并且要做去重——Kafka 是"至少一次"投递,同一条消息可能来两次
          (消费者处理完、提交 offset 之前崩溃,重启后会被重新投递)。Day 8 现在这个处理
          是只读的,天然幂等,重复来也无害;从 Day 11 开始有写库动作,就必须靠 upload_id 去重。
"""
import logging

from app.consumers.events import UploadEvent
from app.consumers.storage import resolve_upload_path

log = logging.getLogger("upload_consumer")


def handle_upload(event: UploadEvent) -> None:
    path = resolve_upload_path(event.storage_key)
    data = path.read_bytes()
    preview = data[:60].decode("utf-8", errors="replace").replace("\n", " ")
    log.info(
        "收到上传 upload_id=%s kind=%s type=%s dept=%s campaign=%s 文件=%s (%d 字节) 预览=%r",
        event.upload_id,
        event.content_kind.value,
        (event.doc_type or event.table_type).value,
        event.department,
        event.campaign_id,
        path.name,
        len(data),
        preview,
    )
