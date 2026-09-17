# 部门权限模型

> 大纲把权限放在 Day 21,但 `department` 字段和 `users` 表要在 **Day 5 设计 schema 时就埋进去**,
> 否则向量表、Text-to-SQL、检索 filter 都要返工。这份文档先把规则定清楚。

## 1. 谁在提问 —— 用户身份

大纲没有登录模块。portfolio 项目用最轻量的方式:

- 建 `users` 表:`user_id, name, department_id, role`
- 请求头带 `X-User-Id`,backend 据此查出 `department_id` 和 `role`,转发给 llm-service
- 以后想展示"会做鉴权",可以升级成简单 JWT,但不是现在的重点

### 角色 role

| role | 能看到的数据 |
|---|---|
| `staff` | 只能看自己部门(`department_id`)的 campaign / 主播数据 + 本部门 SOP + 通用 SOP |
| `manager` | 能看本部门 + 其管理的下属部门(先简化为:能看全部) |
| `admin` | 全部 |

先实现 `staff` vs `非 staff` 两档就够,`manager` 的细粒度留到有余力再做。

## 2. 数据侧过滤(Text-to-SQL)

- 每张业务表(`campaigns` / `anchors` / `campaign_metrics` 通过 join)都有 `department_id`
- LLM 生成 SQL 后,**在执行前**由代码强制拼上过滤条件,不靠 LLM 自觉:
  - `role = staff` → `WHERE department_id = :current_dept`(或 `AND` 进已有 WHERE)
  - `role in (manager, admin)` → 不加
- 同时保留大纲 Day 16 的安全限制:只允许 `SELECT`,拒绝 `DROP` / `DELETE` / `UPDATE` / `INSERT`
- 实现上建议:用 SQL 解析库(如 `sqlglot`)解析 AST,校验是纯 SELECT + 注入 department 谓词,比字符串拼接稳

## 3. 文档侧过滤(向量检索)

- `doc_chunks.metadata` 里存 `department`(取值:某个部门 或 `ALL`)
- 检索时带 metadata filter:
  - `role = staff` → `department IN (:current_dept, 'ALL')`
  - `role in (manager, admin)` → 不加(或 `department IN (所有部门, 'ALL')`)
- SOP 文档在录入时(上传事件的消息体)就要带 `department`;通用流程填 `ALL`
- `campaign_plan`/`campaign_review` 类型的文档同样走这条过滤规则(它们的 `department` 取值
  跟对应 campaign 一致,不会是 `ALL`);另外还能按 `campaign_id`/`doc_type` 精确过滤到
  "只查这一场活动的复盘",这两个字段也存在 `doc_chunks.metadata` 里,不用 join

## 4. 跨部门 campaign 怎么办

一个 campaign 可能涉及多个部门。两种做法,先选简单的:

| 方案 | 说明 | 取舍 |
|---|---|---|
| **A. 单一 owner 部门**(先用这个) | `campaigns.department_id` 只记主责部门 | 简单;跨部门协作的可见性靠 SOP 里写"找 X 部门协同" |
| B. 多对多 | 加 `campaign_departments(campaign_id, department_id)` 关联表 | 更真实,但 SQL 过滤和 schema 都复杂一档 |

## 5. 验收清单(对应大纲 Day 21)

- [ ] 换一个 `staff` 用户提"别的部门 campaign 的 GMV",拿不到数据 / 明确提示无权限
- [ ] 同一个问题换 `admin` 用户,能拿到
- [ ] 文档检索:`staff` 问别的部门专属 SOP,检索不到;问通用 SOP(`ALL`)能检索到
- [ ] LLM 生成了带 `department_id` 过滤的 SQL,即使用户问句里没提部门

## 6. 主播这一层,先不做权限

`campaigns.anchor_id` / `documents.campaign_id -> campaigns.anchor_id` 只是用来**追溯**
"这场活动、这篇策划案/复盘是哪个主播的",不是权限过滤的维度。1000 人 / 10 部门 /
5 组 / 组 20 人的规模假设隐含了"主播组"这一级组织架构,讨论过要不要连带做"组长只看
本组"这种更细粒度的权限,决定先不做——部门这一级的权限先跑通、验证完整,组这层
以后再加,不在这轮范围内。
