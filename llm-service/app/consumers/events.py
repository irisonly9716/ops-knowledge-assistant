"""document-upload-events 消息的 Python 侧模型。

这是 backend-service(Java,生产者)和本服务(消费者)之间的【契约】的 Python 一半。
另一半是 Java 的 UploadEvent.java,共同的事实来源是仓库根目录的
contracts/upload-event.examples.json——两边的测试都对着这份文件验证,见 tests/test_events.py。

下面几个枚举的取值必须跟三个地方保持一致:Java 的枚举、db/migration/ 里的 CHECK 约束、
这里。"同一份事实,三种语言各存一份"迟早漂移,所以 tests/test_events.py 里有测试
直接解析 db/migration/ 下的迁移文件,对比这里的取值——有人改了 SQL 忘了改这里,测试会红。
"""
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel


class ContentKind(StrEnum):
    DOCUMENT = "document"      # SOP / 策划案 / 复盘:非结构化文字,走 RAG
    DATA_TABLE = "data_table"  # 商品表 / 顾客行为表:结构化表格,解析后直接落表


class DocType(StrEnum):
    SOP = "sop"
    CAMPAIGN_PLAN = "campaign_plan"
    CAMPAIGN_REVIEW = "campaign_review"

    @property
    def needs_campaign(self) -> bool:
        """跟 Java 的 DocType.needsCampaign() 同一条规则:只有 sop 可以不挂 campaign。"""
        return self is not DocType.SOP


class TableType(StrEnum):
    CAMPAIGN_PRODUCTS = "campaign_products"
    CAMPAIGN_CUSTOMER_BEHAVIOR = "campaign_customer_behavior"


class Source(StrEnum):
    MANUAL = "manual"
    AUTO = "auto"


class UploadEvent(BaseModel):
    """Java 那边发的是 camelCase(uploadId),这里用 snake_case 属性(upload_id),
    alias_generator=to_camel 负责两边的名字翻译。

    未知字段直接忽略(pydantic 默认行为),不设 extra="forbid":这叫"宽容的读者"——
    生产者以后新增字段时,老版本的消费者不该因此崩溃。反过来,缺必填字段、枚举值不认识
    则严格拒绝,因为那意味着契约真的对不上了。
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    upload_id: str = Field(min_length=1)
    storage_key: str = Field(min_length=1)
    content_kind: ContentKind
    doc_type: DocType | None = None
    table_type: TableType | None = None
    department: str = Field(min_length=1)
    campaign_id: int | None = None
    category: str | None = None
    source: Source

    @model_validator(mode="after")
    def _check_business_rules(self) -> "UploadEvent":
        """生产者(UploadController)已经校验过一遍,这里再校验是防御:
        以后会有别的生产者(比如"定时任务自动上传"),不能默认它们都写对了。"""
        if self.content_kind is ContentKind.DOCUMENT:
            if self.doc_type is None:
                raise ValueError("contentKind=document 时 docType 必填")
            if self.doc_type.needs_campaign and self.campaign_id is None:
                raise ValueError(f"docType={self.doc_type.value} 必须带 campaignId")
            if not self.doc_type.needs_campaign and self.campaign_id is not None:
                raise ValueError("docType=sop 不能带 campaignId")
        else:  # DATA_TABLE
            if self.table_type is None:
                raise ValueError("contentKind=data_table 时 tableType 必填")
            if self.campaign_id is None:
                raise ValueError("contentKind=data_table 必须带 campaignId")
        return self
