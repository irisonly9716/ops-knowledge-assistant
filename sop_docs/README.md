# SOP 文档

Day 5 手写的模拟 SOP,markdown 格式,Day 6+ 起会被 Kafka 消费者读取、解析、切片、
生成 embedding,最终存进 `documents`(`doc_type='sop'`)+ `doc_chunks` 表(见 `docs/data-schema.md`)。

`documents` 表还有 `campaign_plan`(策划案)/ `campaign_review`(复盘,含 ROI 等结果数据)
两种文档类型,跟具体 campaign 挂钩——这两种目前只有 schema,还没有示例内容,不放在这个目录
(这个目录名字就叫 SOP,专指 `doc_type='sop'` 这一种)。

## 格式约定

每篇文档顶部有一段 frontmatter(`---` 包起来),对应 `documents` 表的字段:

```markdown
---
title: 文档标题
department: 部门名(必须跟 departments 表里的 name 完全一致)或 ALL(全部门通用)
category: 分类,如 风控 / 上线checklist / 复盘
responsible_team: 主责/协同团队
---

正文……
```

`department` 目前用得到的值:`美妆运营` / `3C数码运营` / `食品生鲜运营` / `日用百货运营` / `ALL`
(前 4 个来自 `llm-service/scripts/generate_mock_data.py` 里的 `DEPARTMENTS` 常量,
两边必须完全一致,拼错字权限过滤会直接查不到)。

## 现有文档

| 文档 | department | category |
|---|---|---|
| 风控问题处理流程.md | ALL | 风控 |
| 新campaign上线checklist.md | ALL | 上线checklist |
| 直播复盘流程-美妆.md | 美妆运营 | 复盘 |
| 主播签约考核流程-3C数码.md | 3C数码运营 | 主播管理 |
| 大促备货核对流程-食品生鲜.md | 食品生鲜运营 | 备货 |
