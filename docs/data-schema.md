# 数据表设计

> Day 5 定稿,真正的 DDL 在 [sql/02_schema.sql](../sql/02_schema.sql)(这份文档是它的说明,
> 字段类型以 SQL 文件为准)。不接真实阿里数据,用 Python + Faker 生成模拟数据
> (见 `llm-service/scripts/generate_mock_data.py`)。

## 结构化业务表

### departments
| 字段 | 类型 | 说明 |
|---|---|---|
| department_id | bigserial PK | |
| name | text unique | 如 "美妆运营" "3C数码运营" "食品生鲜运营" "日用百货运营" |

### users
| 字段 | 类型 | 说明 |
|---|---|---|
| user_id | bigserial PK | |
| name | text | |
| department_id | bigint FK → departments | 该员工所属部门 |
| role | text | `staff` / `manager` / `admin` |

> 没有"主播组"这一层。1000 人 / 10 部门 / 5 组 / 组 20 人的规模假设隐含了组这级组织架构,
> 讨论过要不要加,决定先不加——组本身、组内权限都先不做,部门这一级权限先跑通再说。

### anchors(主播)
| 字段 | 类型 | 说明 |
|---|---|---|
| anchor_id | bigserial PK | |
| name | text | |
| department_id | bigint FK | 归属部门 |
| category | text | 带货类目 |
| fans_count | int | |
| join_date | date | |

### campaigns(活动)
| 字段 | 类型 | 说明 |
|---|---|---|
| campaign_id | bigserial PK | |
| name | text | |
| department_id | bigint FK | **主责部门 —— 权限过滤基准** |
| anchor_id | bigint FK → anchors | **主责主播**(简化:一个 campaign 记一个主播,不处理多主播联播) |
| category | text | 如 "大促" "新品首发" "日常" |
| start_date | date | |
| end_date | date | |
| status | text | planned / running / finished |

### campaign_metrics(活动指标,按天)

只放"干净的、系统自己就能拿到的数字"。ROI、商品级拆解、成因分析这些需要判断/归因的内容
**不放在这里**——那些是"内容",走 `documents` 表(`campaign_review` 类型)的 RAG 链路。

| 字段 | 类型 | 说明 |
|---|---|---|
| id | bigserial PK | |
| campaign_id | bigint FK → campaigns | |
| date | date | |
| gmv | numeric | 成交额 |
| dau | int | |
| watch_time | numeric | 平均观看时长(分钟) |
| conversion_rate | numeric | 转化率 |
| peak_concurrent_viewers | int | 当天场次在线人数峰值 |
| avg_dwell_time_minutes | numeric | 当天场次人均停留时长(分钟) |

> 部门过滤走 `campaign_metrics JOIN campaigns ON ... WHERE campaigns.department_id = :dept`

### customer_insights(顾客人群分析)—— 提议中,还没建表

复购率、近期在哪些直播间购买过、IP 地址、人均购买价值这些,**不是我们自己算的**——
现实场景里这是另一个团队(用户增长/数据中台)算好、同步给我们的数据,我们只存不算。
项目的核心价值是"AI 分析内容和结果",不是重新造一套用户行为分析引擎。

提议的字段(待确认是否现在建):

| 字段 | 类型 | 说明 |
|---|---|---|
| customer_ref | text PK | 匿名化的顾客标识,不存真实姓名/手机号 |
| ip_address | text | |
| repurchase_rate | numeric | |
| avg_order_value | numeric | |
| recent_campaigns | jsonb | 最近购买过的 campaign/直播间列表,原样存外部系统给的结构,不强行拆表 |

## 文档表(SOP / 策划案 / 复盘)

三种文档共用一张表,靠 `doc_type` 区分,因为它们走的是**同一条摄入管道**
(上传 -> Kafka -> 解析 -> 切片 -> embedding),没必要分三张表、写三遍摄入逻辑。

| doc_type | 什么时候产生 | 内容 |
|---|---|---|
| `sop` | 不定期 | 运营 SOP / 指南,可以是 `ALL`(全部门通用) |
| `campaign_plan` | campaign 开播前上传 | 直播策划案——给 AI 做"根据历史结果优化/预测"用,且要留存 |
| `campaign_review` | campaign 结束后上传 | 复盘文档——**ROI、商品级数据这些都在这篇文档的内容里**,不是结构化字段 |

### documents(原名 sop_documents)
| 字段 | 类型 | 说明 |
|---|---|---|
| doc_id | bigserial PK | |
| doc_type | text | `sop` / `campaign_plan` / `campaign_review` |
| title | text | 如 "风控问题处理流程" |
| department | text | 部门名 或 `ALL`(仅 `sop` 可用)——非外键,原因见下 |
| campaign_id | bigint FK → campaigns,可空 | 仅 `campaign_plan`/`campaign_review` 填;`sop` 必须为空(数据库 CHECK 约束强制) |
| category | text | 如 "风控" "上线checklist" "复盘" |
| steps | jsonb | 结构化步骤,主要给 `sop` 用,见下 |
| responsible_team | text | 主责/协同团队 |
| source_path | text | 原始文件路径 |
| created_at | timestamptz | |

`department` 是 TEXT 不是外键:要支持 `'ALL'` 这个哨兵值,这不是 `departments` 表里的一条真实记录
(代价:部门改名要手动同步这里,量小可接受)。`campaign_plan`/`campaign_review` 类型的
`department` 应该跟对应 `campaign` 的部门一致——通过 `documents.campaign_id -> campaigns.anchor_id`
可以反查出这篇文档是哪个主播的,不在 `documents` 上冗余存一份 `anchor_id`。

`steps` jsonb 结构(方便直接回答"第一步/最后一步/找哪个部门"):
```json
[
  {"order": 1, "action": "在风控后台确认命中的规则和账号", "owner": "运营本人"},
  {"order": 2, "action": "填写申诉工单,附上直播回放片段",   "owner": "运营本人"},
  {"order": 3, "action": "同步给风控对接人,等待复核",       "owner": "风控组"}
]
```

### doc_chunks(切片 + 向量)
| 字段 | 类型 | 说明 |
|---|---|---|
| chunk_id | bigserial PK | |
| doc_id | bigint FK → documents | |
| chunk_index | int | 第几片 |
| content | text | 切片正文 |
| embedding | vector(1536) | 维度跟 embedding 模型对齐(text-embedding-3-small = 1536) |
| metadata | jsonb | `{department, category, source_doc, title, doc_type, campaign_id}` —— 检索时按 department/doc_type/campaign 过滤,不用 join |

建索引:`CREATE INDEX ON doc_chunks USING hnsw (embedding vector_cosine_ops);`

### doc_ingest_status(处理状态)
| 字段 | 类型 | 说明 |
|---|---|---|
| doc_id | bigint PK → documents | |
| status | text | uploading / processing / done / failed |
| error | text | 失败原因 |
| updated_at | timestamptz | |

> 也可以只用 Redis 存 `doc:{id}:status`,落库的好处是能查历史。

## 模拟数据规模(已生成)

- 4 个部门(美妆运营 / 3C数码运营 / 食品生鲜运营 / 日用百货运营)
- 24 个用户(1 admin,4 manager,19 staff)
- 15 个主播(每部门 3-5 个)
- 49 个 campaign(每部门 10-15 个,每个 15-60 天的 metrics,共 1700+ 条指标记录)
- 5 篇 SOP(2 篇 `ALL` 通用 + 3 篇分部门),放 [sop_docs/](../sop_docs/) 目录
- `campaign_plan` / `campaign_review` 示例文档:待定(schema 已就绪,内容还没写)
