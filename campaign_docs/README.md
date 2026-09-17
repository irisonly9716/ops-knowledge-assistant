# Campaign 文档(策划案 / 复盘)

`documents` 表里 `doc_type = campaign_plan` / `campaign_review` 的模拟内容,markdown 格式,
跟 [sop_docs/](../sop_docs/) 走同一条摄入管道,分开放目录只是因为语义不同。

## 格式约定

```markdown
---
doc_type: campaign_plan 或 campaign_review
title: 文档标题
department: 部门名(必须跟 departments 表的 name 完全一致)
campaign_name: 对应的 campaign 名字
anchor: 主责主播名字
---

正文……
```

`campaign_name` + `department` 是给摄入代码用来反查 `campaign_id` 的(`documents.campaign_id`
是 NOT NULL 外键,但这几篇文件是静态内容,写的时候数据库里的 campaign_id 是多少无所谓,
交给 Day 6+ 的摄入逻辑按名字匹配)。这跟 SOP 文档里 `department` 靠字符串匹配是同一个思路。

## 两种文档的定位区别

- **campaign_plan**(策划案):activity 开播前写的,选品思路、话术策略、预期指标——
  用来让 AI 事后对照实际结果,分析"哪些策划特点导致了这个结果",也给未来类似
  campaign 的策划提供参考
- **campaign_review**(复盘):activity 结束后写的,**ROI、商品级数据这些结果性的内容
  都在这篇文档里**,不是结构化字段——这是本项目"AI 分析内容,而不是重新计算指标"
  这条原则的直接体现

## 现有文档

以真实生成的 mock 数据为原型(`campaign_id=3`,美妆运营,`美妆新品首发`,主播顾彬):

| 文档 | doc_type |
|---|---|
| 美妆新品首发-策划案.md | campaign_plan |
| 美妆新品首发-复盘.md | campaign_review |
