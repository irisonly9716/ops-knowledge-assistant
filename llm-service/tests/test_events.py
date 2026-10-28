"""消息契约(Python 一半)的测试。

最重要的两个:
  1. 契约文件里的每条样例都能被解析——Java 那边的 UploadEventContractTest 验证的是同一份文件
  2. 枚举取值跟 db/migration/ 里的 CHECK 约束对得上——"同一份事实三种语言各存一份"的漂移检测
"""
import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.consumers.events import ContentKind, DocType, Source, TableType, UploadEvent

REPO_ROOT = Path(__file__).resolve().parents[2]  # tests/ -> llm-service/ -> 仓库根
CONTRACT = json.loads((REPO_ROOT / "contracts" / "upload-event.examples.json").read_text(encoding="utf-8"))


def _migration_version(path: Path) -> tuple[int, ...]:
    """V3__xxx.sql -> (3,),V3_1__xxx.sql / V3.1__xxx.sql -> (3, 1)。
    按数字比较,V10 才会排在 V9 后面(按字符串排序 "V10" < "V9")。"""
    return tuple(int(part) for part in re.split(r"[._]", path.name[1:].split("__", 1)[0]))


MIGRATIONS = sorted((REPO_ROOT / "db" / "migration").glob("V*__*.sql"), key=_migration_version)
# 所有迁移按版本顺序拼起来,就是当前 schema 的完整历史
SCHEMA_SQL = "\n".join(path.read_text(encoding="utf-8") for path in MIGRATIONS)


def base(**overrides) -> dict:
    """一条合法的 sop 事件,测试里在它基础上改一两个字段。"""
    event = {
        "uploadId": "u-1", "storageKey": "u-1-a.md", "contentKind": "document", "docType": "sop",
        "tableType": None, "department": "ALL", "campaignId": None, "category": None, "source": "manual",
    }
    event.update(overrides)
    return event


# ------------------------------------------------------------------ 契约文件


@pytest.mark.parametrize("example", CONTRACT, ids=lambda e: e["uploadId"][:8])
def test_every_contract_example_parses(example):
    event = UploadEvent.model_validate(example)
    assert event.upload_id == example["uploadId"]
    assert event.storage_key == example["storageKey"]


def test_contract_examples_cover_every_enum_value():
    """契约样例必须覆盖每一个枚举取值,否则新增取值时两边都没人验证。"""
    events = [UploadEvent.model_validate(e) for e in CONTRACT]
    assert {e.content_kind for e in events} == set(ContentKind)
    assert {e.doc_type for e in events if e.doc_type} == set(DocType)
    assert {e.table_type for e in events if e.table_type} == set(TableType)
    assert {e.source for e in events} == set(Source)


# ------------------------------------------------------------------ 跨语言漂移检测


def test_migrations_are_found():
    # 目录或文件名规则变了时,下面的测试会因为"什么都没解析到"而失去意义,这里先拦住
    assert [p.name.split("__")[0] for p in MIGRATIONS][:3] == ["V1", "V2", "V3"]


@pytest.mark.parametrize(("column", "enum"), [
    ("doc_type", DocType),        # documents、uploads 两张表都有
    ("table_type", TableType),    # uploads
    ("content_kind", ContentKind),  # uploads
    ("source", Source),           # uploads
])
def test_enum_values_match_every_sql_check_constraint(column, enum):
    # 局限:如果以后有迁移改了某个 CHECK 的取值,旧的定义还留在更早的迁移文件里,
    # 这里会误报。到那时改成只看最新的定义,或者改成连真实数据库读约束。
    checks = re.findall(rf"CHECK\s*\(\s*{column}\s+IN\s*\(([^)]*)\)\s*\)", SCHEMA_SQL)
    assert checks, f"迁移文件里没找到 {column} 的 CHECK 约束,正则或 SQL 变了"
    for check in checks:
        assert set(re.findall(r"'([^']+)'", check)) == {v.value for v in enum}, (
            f"{column} 的 CHECK 约束跟 Python 的 {enum.__name__} 枚举不一致了——"
            "改数据库约束时要同步改 events.py 和 Java 里对应的枚举"
        )


def test_table_type_values_are_real_tables_in_the_schema():
    tables = set(re.findall(r"CREATE TABLE (\w+)", SCHEMA_SQL)) - set(re.findall(r"DROP TABLE (\w+)", SCHEMA_SQL))
    for table_type in TableType:
        assert table_type.value in tables, f"TableType.{table_type.name} 在 SQL schema 里没有对应的表"


# ------------------------------------------------------------------ 解析行为


def test_unknown_extra_fields_are_ignored_for_forward_compatibility():
    # 生产者以后新增字段,老版本消费者不该因此崩溃("宽容的读者")
    assert UploadEvent.model_validate(base(brandNewField="x")).upload_id == "u-1"


def test_accepts_snake_case_too_so_tests_and_scripts_can_build_events_naturally():
    event = UploadEvent.model_validate(
        {"upload_id": "u", "storage_key": "k", "content_kind": "document", "doc_type": "sop",
         "department": "ALL", "source": "manual"})
    assert event.doc_type is DocType.SOP


@pytest.mark.parametrize("bad", [
    pytest.param(base(uploadId=""), id="空 uploadId"),
    pytest.param(base(storageKey=""), id="空 storageKey"),
    pytest.param(base(contentKind="video"), id="未知 contentKind"),
    pytest.param(base(docType="SOP"), id="大写 SOP(线上约定小写)"),
    pytest.param(base(docType="campain_plan"), id="docType 拼错"),
    pytest.param(base(source="robot"), id="未知 source"),
    pytest.param(base(docType=None), id="document 缺 docType"),
    pytest.param(base(docType="campaign_review", campaignId=None), id="review 缺 campaignId"),
    pytest.param(base(campaignId=3), id="sop 带了 campaignId"),
    pytest.param(base(contentKind="data_table", docType=None, tableType=None, campaignId=3), id="data_table 缺 tableType"),
    pytest.param(base(contentKind="data_table", docType=None, tableType="campaign_products", campaignId=None), id="data_table 缺 campaignId"),
    pytest.param({"uploadId": "u"}, id="缺一大堆必填字段"),
    pytest.param("不是对象", id="根本不是 JSON 对象"),
])
def test_contract_violations_are_rejected(bad):
    with pytest.raises(ValidationError):
        UploadEvent.model_validate(bad)


def test_only_sop_can_go_without_a_campaign():
    assert DocType.SOP.needs_campaign is False
    assert DocType.CAMPAIGN_PLAN.needs_campaign is True
    assert DocType.CAMPAIGN_REVIEW.needs_campaign is True
