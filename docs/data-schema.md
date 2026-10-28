# 数据表设计

> 真正的 DDL 在 [db/migration/](../db/migration/) 下的 Flyway 迁移文件里,按版本号顺序执行
> (这份文档是它们的说明,字段类型以 SQL 文件为准)。不接真实阿里数据,用 Python + Faker 生成模拟数据
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
| department_id | bigint FK → departments,可空 | 该员工所属部门。`executive` 和 `admin` 不属于任何运营部门,必须为空;`staff` 和 `manager` 必须有(CHECK 约束强制) |
| role | text | `staff` / `manager` / `executive` / `admin`,各自能做什么见 [permission-model.md](permission-model.md) |

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
| has_plan_doc | boolean | 有没有策划案文档,默认 false |
| has_review_doc | boolean | 有没有复盘文档,默认 false |
| planned_operating_cost | numeric | 计划运营成本(坑位费+推广+赠品,不含商品成本),策划阶段就能填 |
| actual_operating_cost | numeric | 实际运营成本,只有活动结束、账单到齐才填得出,可以为空 |

**ROI 怎么算**(踩过两次坑才定下来的公式,记录一下避免以后又算错):

```
ROI = SUM(campaign_products.profit) / campaigns.actual_operating_cost
```

- 分子是 `campaign_products.profit` 的汇总——这个字段**已经扣过商品成本**了(利润 = 销售额 - 成本价×销量),不能再在分母里重复扣一次商品成本
- 分母只用**运营成本**(这场活动"额外花的营销投入"),不包含商品成本——商品成本是做生意本来就要花的钱,不是这场活动新增的投入,ROI 问的是"这笔营销投入划不划算",不是"整个生意划不划算"
- 计划成本 vs 实际成本分开存,是因为活动结束后账单和预算总会有出入(mock 数据里两者故意不完全相等)
`has_plan_doc`/`has_review_doc` 是**效率优化,不是权威数据源**——真相永远是"`documents`
表里存不存在这个 `campaign_id` 对应 `doc_type` 的一行"。这两个字段只是让路由逻辑在
决定要不要做一次向量检索之前,先用一次便宜的查询判断"根本没有文档,不用检索了"。
代价:摄入代码插入 `documents` 行时,必须记得同步更新这两个字段,这是主动
接受的冗余,不是疏忽。

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

### campaign_products(商品维度售卖数据)—— 运营上传,不是我们计算的

| 字段 | 类型 | 说明 |
|---|---|---|
| id | bigserial PK | |
| campaign_id | bigint FK → campaigns | |
| product_name | text | 没建单独的商品目录表,先用文本存商品名(见下方"没选的做法") |
| units_sold | int | 销量 |
| sell_through_rate | numeric | 售出率 |
| return_rate | numeric | 退货率 |
| sales_amount | numeric | 销售额 |
| click_through_rate | numeric | 点击率 |
| cost_price | numeric | 单件成本价 |
| profit | numeric | 这个商品在这场活动的利润总额 |
| profit_margin | numeric | 利润率 |

`cost_price`/`profit`/`profit_margin` 是后来补的:ROI 不能只拿 GMV 减一个笼统的
"成本"就算,必须扣掉商品成本(`cost_price × units_sold` 汇总),这几个字段就是让
"商品成本"变成结构化查询就能拿到的数字,不用在复盘文档里手动估算。这三个字段
现实中应该跟其它几列一样,是运营上传的表格自带的(财务系统算好给的),不是
我们自己计算再存进去的。

### campaign_customer_behavior(顾客行为数据)—— 运营上传,不是我们计算的

复购率这类"人群分析"结论,**不是我们自己算的**——现实场景里这本该是另一个团队
(用户增长/数据中台)算好同步给我们的东西。但这张表存的是**原始的顾客-campaign
行为记录**,不是"已经算好的复购率指标",好处是复购率这种问题以后想问,
是一句普通 SQL(按 `customer_ref` 分组、数一下出现在几个不同 campaign 里),
不需要额外建一套订单/交易系统去支撑计算。

| 字段 | 类型 | 说明 |
|---|---|---|
| id | bigserial PK | |
| campaign_id | bigint FK → campaigns | |
| customer_ref | text | 匿名化顾客标识,不存真实姓名/手机号;建了索引方便跨 campaign 找同一顾客 |
| region | text | |
| dwell_time_minutes | numeric | 这个顾客在这场直播的停留时长 |
| products_purchased | jsonb | 买了哪些商品,数组 |
| purchase_amount | numeric | |
| ip_address | text | |

`campaign_products` / `campaign_customer_behavior` 是**结构化表格上传**(运营导出的
Excel/CSV 之类),跟下面 `documents`(非结构化文字)是两条不同的摄入管道——前者解析后
直接 `INSERT` 成行、走 Text-to-SQL 查询;后者要切片 + embedding、走 RAG 检索。
见 [architecture.md](architecture.md) 的说明。

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
| doc_id | bigserial PK | 覆盖新版本时不变(原地更新这一行、重建 chunk),所以引用和 eval 标注不会失效 |
| upload_id | uuid unique FK → uploads | 产生当前版本的那次上传 |
| doc_type | text | `sop` / `campaign_plan` / `campaign_review` |
| title | text | 如 "风控问题处理流程" |
| department | text | 部门名 或 `ALL`(仅 `sop` 可用)——非外键,原因见下 |
| campaign_id | bigint FK → campaigns,可空 | 仅 `campaign_plan`/`campaign_review` 填;`sop` 必须为空(数据库 CHECK 约束强制) |
| category | text | 如 "风控" "上线checklist" "复盘" |
| steps | jsonb | 结构化步骤,主要给 `sop` 用,见下 |
| responsible_team | text | 主责/协同团队 |
| source_path | text | 原始文件路径 |
| created_at | timestamptz | |

**自然键**(认定"同一份文档",重传时按它判断是新建还是覆盖):
- 策划案 / 复盘:`(campaign_id, doc_type)` 唯一,一场活动每种只有一份。`sop` 的 `campaign_id` 是 NULL,
  唯一约束里 NULL 和 NULL 不算相等,所以这条约束只管活动文档。
- SOP:同一 `department`(或 `ALL`)下标题唯一,按规范化后的标题比较(NFKC 全角转半角 + 去首尾空格 + 小写),
  所以 `"　风控问题处理流程 "` 和 `"风控问题处理流程"` 算同一份。表里存的仍是原标题。

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
| doc_id | bigint not null | 跟 `department` 一起构成指向 documents 的复合外键 |
| department | text | **有意的冗余**:检索和 RLS 直接按这一列过滤,不用 JOIN |
| chunk_index | int | 第几片,`(doc_id, chunk_index)` 唯一 |
| content | text | 切片正文 |
| embedding | vector(1536) | 维度跟 embedding 模型对齐(text-embedding-3-small = 1536) |
| embedding_model | text | 生成这条向量的模型。只能区分同维度的模型;换不同维度的模型要另加列或另建表 |
| metadata | jsonb | `{category, source_doc, title, doc_type, campaign_id}` 等其余检索信息 |

冗余的 `department` 靠**复合外键**保证不出错:`(doc_id, department)` 引用 `documents(doc_id, department)`,
所以 chunk 的部门只能等于所属文档的部门,写错了数据库直接拒绝。`ON UPDATE CASCADE`:文档改部门时
chunk 自动跟着改;`ON DELETE CASCADE`:删文档时 chunk 一起删。

索引:`embedding` 上的 HNSW(`vector_cosine_ops`)、`department` 上的普通索引(小部门走精确扫描时先按部门筛)。

### uploads(上传记录 + 处理状态)
每次上传一行。后端收到上传时先写 `received`(需要审批的写 `pending_approval`),再发 Kafka;
消费者处理时更新状态。两条管道(文档、数据表)共用。

| 字段 | 类型 | 说明 |
|---|---|---|
| upload_id | uuid PK | 跟 Kafka 消息里的 `uploadId` 是同一个值,也是消费者做幂等的依据 |
| source | text | `manual` / `auto` |
| uploaded_by | bigint FK → users,可空 | 人工上传必填,自动同步为空(CHECK 约束强制) |
| content_kind / doc_type / table_type | text | 跟消息契约同一套取值和组合规则 |
| department | text | 部门名,或 `ALL`(仅 `sop`) |
| campaign_id | bigint FK → campaigns,可空 | 活动文档和数据表必填 |
| title | text | `sop` 必填(自然键的一部分);活动文档的标题由系统生成 |
| original_filename / storage_key | text | 原始文件名 / 存储位置(`storage_key` 唯一) |
| replaces_upload_id | uuid FK → uploads,可空 | 要覆盖的当前版本是哪次上传产生的,为空表示新建 |
| status | text | `pending_approval` / `received` / `processing` / `done` / `failed` / `rejected` |
| error | text | 失败原因 |
| reviewed_by / reviewed_at / review_comment | | 审批人、审批时间、审批意见。审批人不能是上传人(CHECK 约束强制) |
| created_at / updated_at | timestamptz | |

```
不需要审批:                     received -> processing -> done / failed
需要审批:pending_approval --批准--> received -> processing -> done / failed
                          --拒绝--> rejected
```

执行覆盖时再核对一次"当前版本还是不是 `replaces_upload_id`":不是的话说明审批期间已被别人覆盖,
这次申请作废(乐观并发控制)。

> 取代了早期的 `doc_ingest_status`:它以 `doc_id` 为主键,文档写库之前还没有 `doc_id`,
> 数据表上传根本不产生文档,两种情况都记不了。

## 模拟数据规模(已生成)

- 4 个部门(美妆运营 / 3C数码运营 / 食品生鲜运营 / 日用百货运营)
- 25 个用户(4 manager,19 staff,1 executive,1 admin;后两个不属于任何部门)
- 15 个主播(每部门 3-5 个)
- 47 个 campaign(每部门 10-15 个,每个 15-60 天的 metrics,共 1700+ 条指标记录;运营成本已校准到 GMV 的 10%-20%)
- 202 条 campaign_products(含成本价/利润/利润率,销售额已校准到不超过所属 campaign 的 GMV),1200 条 campaign_customer_behavior
- 5 篇 SOP(2 篇 `ALL` 通用 + 3 篇分部门),放 [sop_docs/](../sop_docs/) 目录
- 1 组策划案 + 复盘示例文档(以 `campaign_id=3` "美妆618大促" 为原型,数字跟当前 mock 数据对得上——但这个耦合比较脆弱,见 [campaign_docs/README.md](../campaign_docs/README.md) 的说明),放 [campaign_docs/](../campaign_docs/) 目录
