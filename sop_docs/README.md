# SOP documents

Hand-written mock SOPs in Markdown. The Kafka consumer reads, parses, chunks and embeds them;
they end up in `documents` (`doc_type='sop'`) + `doc_chunks` (see `docs/data-schema.md`).

`documents` also holds `campaign_plan` and `campaign_review` documents, which belong to a
specific campaign. Those live in [campaign_docs/](../campaign_docs/); this folder is only for
`doc_type='sop'`.

## Format

Each document starts with a frontmatter block (between `---` lines) that maps to `documents` columns:

```markdown
---
title: Document title
department: Department name (must match departments.name exactly) or ALL (every department)
category: Category, e.g. Trust & Safety / Launch checklist / Review
responsible_team: Owning team
---

Body...
```

Department values in use: `Beauty` / `Consumer Electronics` / `Fresh Grocery` / `Home Goods` /
`ALL`. The first four come from `DEPARTMENTS` in `llm-service/scripts/generate_mock_data.py`;
both sides must match exactly, or permission filtering silently finds nothing.

## Documents

| Document | department | category |
|---|---|---|
| account-restriction-handling.md | ALL | Trust & Safety |
| new-campaign-launch-checklist.md | ALL | Launch checklist |
| livestream-review-process-beauty.md | Beauty | Review |
| host-onboarding-and-review-electronics.md | Consumer Electronics | Host management |
| sale-event-inventory-check-fresh-grocery.md | Fresh Grocery | Inventory |
