# Campaign documents (plans / reviews)

Mock content for `documents` rows with `doc_type = campaign_plan` / `campaign_review`, in
Markdown. They go through the same ingestion pipeline as [sop_docs/](../sop_docs/); they live in
a separate folder only because they mean something different.

## Format

```markdown
---
doc_type: campaign_plan or campaign_review
title: Document title
department: Department name (must match departments.name exactly)
campaign_name: Name of the campaign
host: Lead host
---

Body...
```

The documents describe one campaign, but `documents.campaign_id` is a NOT NULL foreign key
whose value only exists in the database. The upload picks the campaign explicitly
(`campaignId`); `campaign_name` and `host` here are for people reading the file.

**The numbers are real mock data.** GMV, conversion rate, operating cost and the product
table come from what `llm-service/scripts/generate_mock_data.py` generates for
`campaign_id = 1`. The generator is seeded and dates are computed from a fixed `AS_OF` date,
so every run produces the same rows and these documents stay correct, as long as the
generator and its pinned faker version don't change. If you change the generator, query the
campaign again and update the numbers here.

## The two document types

- **campaign_plan**: written before the campaign goes live: product lineup, pricing and
  targets. AI later compares it with the results to explain which choices led to which
  outcome, and it is a reference for planning similar campaigns.
- **campaign_review**: written after the campaign: **ROI and per-product results live in
  this document**, not in structured fields. That is the project's principle in practice:
  AI analyzes content and results, it does not recompute metrics.

## Documents

Based on the generated mock data (`campaign_id = 1`, Beauty, `Beauty Black Friday Live`,
host Tommy Walter):

| Document | doc_type |
|---|---|
| beauty-black-friday-live-plan.md | campaign_plan |
| beauty-black-friday-live-review.md | campaign_review |
