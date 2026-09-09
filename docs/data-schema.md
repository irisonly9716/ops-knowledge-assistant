# 数据表设计(草案)

> Day 5 定稿。这里先给一版把 **权限字段(`department_id`)** 和 **SOP 结构化字段** 都埋好的草案。
> 不接真实阿里数据,用 Python + Faker 生成模拟数据。

## 结构化业务表

### departments
| 字段 | 类型 | 说明 |
|---|---|---|
| department_id | serial PK | |
| name | text | 如 "美妆运营" "3C运营" "食品运营" |

### users
| 字段 | 类型 | 说明 |
|---|---|---|
| user_id | serial PK | |
| name | text | |
| department_id | int FK → departments | 该员工所属部门 |
| role | text | `staff` / `manager` / `admin` |

### anchors(主播)
| 字段 | 类型 | 说明 |
|---|---|---|
| anchor_id | serial PK | |
| name | text | |
| department_id | int FK | 归属部门 |
| category | text | 带货类目 |
| fans_count | int | |
| join_date | date | |

### campaigns(活动)
| 字段 | 类型 | 说明 |
|---|---|---|
| campaign_id | serial PK | |
| name | text | |
| department_id | int FK | **主责部门 —— 权限过滤基准** |
| category | text | 如 "大促" "新品首发" "日常" |
| start_date | date | |
| end_date | date | |
| status | text | planned / running / finished |

### campaign_metrics(活动指标,按天)
| 字段 | 类型 | 说明 |
|---|---|---|
| id | serial PK | |
| campaign_id | int FK → campaigns | |
| date | date | |
| gmv | numeric | 成交额 |
| dau | int | |
| watch_time | numeric | 平均观看时长(分钟) |
| conversion_rate | numeric | 转化率 |

> 部门过滤走 `campaign_metrics JOIN campaigns ON ... WHERE campaigns.department_id = :dept`

## SOP 文档

### sop_documents(文档元数据)
| 字段 | 类型 | 说明 |
|---|---|---|
| doc_id | serial PK | |
| title | text | 如 "风控问题处理流程" |
| department | text | 部门名 或 `ALL`(通用) |
| category | text | 如 "风控" "上线checklist" "复盘" |
| steps | jsonb | 结构化步骤,见下 |
| responsible_team | text | 主责/协同团队 |
| source_path | text | 原始文件路径 |
| created_at | timestamptz | |

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
| chunk_id | serial PK | |
| doc_id | int FK → sop_documents | |
| chunk_index | int | 第几片 |
| content | text | 切片正文 |
| embedding | vector(1536) | 维度跟 embedding 模型对齐(text-embedding-3-small = 1536) |
| metadata | jsonb | `{department, category, source_doc, title}` —— 检索时按 department 过滤 |

建索引:`CREATE INDEX ON doc_chunks USING hnsw (embedding vector_cosine_ops);`

### doc_ingest_status(处理状态)
| 字段 | 类型 | 说明 |
|---|---|---|
| doc_id | int PK | |
| status | text | uploading / processing / done / failed |
| error | text | 失败原因 |
| updated_at | timestamptz | |

> 也可以只用 Redis 存 `doc:{id}:status`,落库的好处是能查历史。

## 模拟数据规模建议

- 3~4 个部门
- 每部门 5~10 个用户(混 staff / manager / 1 个 admin)
- 每部门 10~20 个 campaign,每个 campaign 15~60 天的 metrics
- 每部门 3~5 个主播
- 3~5 篇 SOP(至少 1 篇 `ALL` 通用),markdown 格式,放 `sop_docs/` 目录
