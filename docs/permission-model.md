# 部门权限模型

> 权限检查可以最后接入,但 `department` 字段和 `users` 表必须在 **设计 schema 时就埋进去**,
> 否则向量表、Text-to-SQL、检索 filter 都要返工。这份文档先把规则定清楚。

## 1. 谁在提问 —— 用户身份

本项目不做登录模块,用最轻量的方式:

- 建 `users` 表:`user_id, name, department_id, role`
- 请求头带 `X-User-Id`,backend 据此查出 `department_id` 和 `role`,转发给 llm-service
- 以后想展示"会做鉴权",可以升级成简单 JWT,但不是现在的重点

### 角色 role

| role | 是谁 | 所属部门 |
|---|---|---|
| `staff` | 运营员工 | 必须有 |
| `manager` | 部门经理,每个部门一位 | 必须有 |
| `executive` | 创始人 / CEO | 无(必须为空) |
| `admin` | 系统管理员,也就是搭这个系统的人 | 无(必须为空) |

"必须有 / 必须为空"由 `users` 表的 CHECK 约束强制。

### 能看什么

| role | 文档 | 业务数据 | 系统数据(审计日志、所有上传记录和失败原因) |
|---|---|---|---|
| `staff` | 本部门 + `ALL` | 本部门 | 不能 |
| `manager` | 本部门 + `ALL` | 本部门 | 不能 |
| `executive` | 全部 | 全部 | 不能:审计日志里有每个员工问过的问题,属于隐私 |
| `admin` | 全部 | 全部 | 能 |

`executive` 和 `admin` 的跨部门访问都会在审计表里单独标记。

### 能改什么 —— 职责分离

**文档和数据表是业务内容,只能由业务人员上传,再由上级审批。** 搭系统的人(`admin`)不碰业务内容;
写内容的人不能批准自己的内容。

| 操作 | staff | manager | executive | admin |
|---|---|---|---|---|
| 新建本部门的文档 / 数据表 | 直接生效 | 直接生效 | 不能 | 不能 |
| 覆盖本部门的文档 / 数据表 | 本部门经理批准 | executive 批准 | 不能 | 不能 |
| 新建或覆盖 `ALL` 文档 | 本部门经理批准 | executive 批准 | 不能 | 不能 |
| 审批 | 不能 | 本部门 staff 的申请 | 所有 manager 的申请 | 不能 |
| 系统维护(重投死信、换模型后重新生成向量、跑 eval) | 不能 | 不能 | 不能 | 能 |

规则可以压缩成一句:**需要审批时,由上传者的上级审批,而且审批人必须看得到被审批的内容。**
别的部门经理看不到本部门专属的文档,所以经理的申请只能交给能看全部部门的 `executive`。

- 系统维护不算"修改内容":它不改变任何文档的内容,也不改变任何审批结果。
- 待审批期间旧版本继续生效,批准后才替换;被拒绝的上传,新文件删除。
- "审批人不能是上传人"由 `uploads` 表的 CHECK 约束强制;"审批人必须是上级"要跨表比对,在应用代码里检查,
  由越权测试覆盖。
- 已知取舍:只有一位 `executive`,所有经理的申请都排到 CEO 一个人那里。真实公司通常会授权给副总分担。

### 已知局限

- 身份是模拟的:请求头 `X-User-Id` 谁都能伪造。真正的登录认证(JWT 等)只写设计、不实现。
  所以准确的说法是:授权规则是真实的、由数据库强制执行;认证是模拟的。
- 职责分离管住的是"通过系统做的操作"。开发者直连数据库仍然能改任何东西;真实环境要靠数据库账号分离
  (迁移账号、应用账号、只读账号各管各的)加上操作审计来约束。

## 2. 数据侧过滤(Text-to-SQL)

- 每张业务表(`campaigns` / `anchors` / `campaign_metrics` 通过 join)都有 `department_id`
- LLM 生成 SQL 后,**在执行前**由代码强制拼上过滤条件,不靠 LLM 自觉:
  - `role in (staff, manager)` → `WHERE department_id = :current_dept`(或 `AND` 进已有 WHERE)
  - `role in (executive, admin)` → 不加
- 同时保留 SQL 的安全限制:只允许 `SELECT`,拒绝 `DROP` / `DELETE` / `UPDATE` / `INSERT`
- 实现上建议:用 SQL 解析库(如 `sqlglot`)解析 AST,校验是纯 SELECT + 注入 department 谓词,比字符串拼接稳

## 3. 文档侧过滤(向量检索)

- `doc_chunks.department` 是一个正式的列(取值:某个部门 或 `ALL`),由复合外键保证跟所属文档一致
- 检索时按这一列过滤:
  - `role in (staff, manager)` → `department IN (:current_dept, 'ALL')`
  - `role in (executive, admin)` → 不加
- SOP 文档在录入时(上传事件的消息体)就要带 `department`;通用流程填 `ALL`
- **⚠️ 向量检索 + WHERE 过滤有个会"安静地少给结果"的坑(实测)**:HNSW 是近似索引,
  它先按向量相似度取出一批候选(`hnsw.ef_search`,默认 40),**再**套 `WHERE department = ...`
  过滤。如果目标部门只占全部数据的 1%,这 40 个候选里大概率一条都不属于它——实测
  20000 行/100 个部门,要"部门 7 的 top 10",默认设置返回 **0 条**(不报错),打开
  `SET hnsw.iterative_scan = relaxed_order`(pgvector ≥ 0.8,本项目装的是 0.8.6)后返回 10 条,
  精确扫描(不走索引)也是 10 条。对权限过滤来说这不只是"召回率低",是**用户明明有权看的
  文档查不出来**。应对办法(写检索函数时选一个):
  1. 本项目 `doc_chunks` 只有几百~几千行,**直接不用 HNSW 索引、走精确扫描**,永远正确,
     且这个量级下速度完全够——索引是数据量大了才需要的优化,不是必需品
  2. 保留索引,检索前 `SET hnsw.iterative_scan = relaxed_order`
  3. 数据量大到必须用索引时,按部门做分区表/部分索引
- `campaign_plan`/`campaign_review` 类型的文档同样走这条过滤规则(它们的 `department` 取值
  跟对应 campaign 一致,不会是 `ALL`);另外还能按 `campaign_id`/`doc_type` 精确过滤到
  "只查这一场活动的复盘",这两个字段也存在 `doc_chunks.metadata` 里,不用 join

## 4. 跨部门 campaign 怎么办

一个 campaign 可能涉及多个部门。两种做法,先选简单的:

| 方案 | 说明 | 取舍 |
|---|---|---|
| **A. 单一 owner 部门**(先用这个) | `campaigns.department_id` 只记主责部门 | 简单;跨部门协作的可见性靠 SOP 里写"找 X 部门协同" |
| B. 多对多 | 加 `campaign_departments(campaign_id, department_id)` 关联表 | 更真实,但 SQL 过滤和 schema 都复杂一档 |

## 5. 验收清单

- [ ] 换一个 `staff` 用户提"别的部门 campaign 的 GMV",拿不到数据 / 明确提示无权限
- [ ] 同一个问题换 `executive` 或 `admin` 用户,能拿到
- [ ] `manager` 跟 `staff` 一样拿不到别的部门的数据
- [ ] `executive` 和 `admin` 上传、覆盖都被拒绝;`admin` 不能审批;任何人不能审批自己的上传
- [ ] 文档检索:`staff` 问别的部门专属 SOP,检索不到;问通用 SOP(`ALL`)能检索到
- [ ] LLM 生成了带 `department_id` 过滤的 SQL,即使用户问句里没提部门

## 6. 主播这一层,先不做权限

`campaigns.anchor_id` / `documents.campaign_id -> campaigns.anchor_id` 只是用来**追溯**
"这场活动、这篇策划案/复盘是哪个主播的",不是权限过滤的维度。1000 人 / 10 部门 /
5 组 / 组 20 人的规模假设隐含了"主播组"这一级组织架构,讨论过要不要连带做"组长只看
本组"这种更细粒度的权限,决定先不做——部门这一级的权限先跑通、验证完整,组这层
以后再加,不在这轮范围内。
