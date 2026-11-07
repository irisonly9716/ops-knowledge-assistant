# Data schema

> The actual DDL lives in the Flyway migrations under [db/migration/](../db/migration/) (V1 pgvector, V2 base
> schema, V3 uploads / roles / chunk department, V4 the `document_catalog` view, V5 its `catalog_entry()` function,
> V6 the `outbox` table, V7 `documents.version` and `uploads.category`, V8 the delivery and republish counts on
> `uploads`, V9 the data dictionary and the metric views), applied in
> version order (this document explains them; the SQL files are authoritative for column types).
> The data is mock data generated with Python + Faker (see `llm-service/scripts/generate_mock_data.py`).

## Structured business tables

### departments
| Column | Type | Notes |
|---|---|---|
| department_id | bigserial PK | |
| name | text unique | e.g. "Beauty", "Consumer Electronics", "Fresh Grocery", "Home Goods" |

### users
| Column | Type | Notes |
|---|---|---|
| user_id | bigserial PK | |
| name | text | |
| department_id | bigint FK → departments, nullable | The employee's department. `executive` and `admin` belong to no ops department and must be NULL; `staff` and `manager` must have one (enforced by a CHECK constraint) |
| role | text | `staff` / `manager` / `executive` / `admin`; what each can do: [permission-model.md](permission-model.md) |

> There is no "host team" level. The required scale (8 departments, 500 people, so about 60 per
> department) suggests a team level in the org chart; it was considered and left out. Teams and team-level
> permissions come after department-level permissions work.

### hosts (livestream hosts)
| Column | Type | Notes |
|---|---|---|
| host_id | bigserial PK | |
| name | text | |
| department_id | bigint FK | owning department |
| category | text | product category the host sells |
| fans_count | int | |
| join_date | date | |

### campaigns
| Column | Type | Notes |
|---|---|---|
| campaign_id | bigserial PK | |
| name | text | e.g. "Beauty Black Friday Live" |
| department_id | bigint FK | **owning department -- the basis of permission filtering** |
| host_id | bigint FK → hosts | **lead host** (simplification: one host per campaign, co-hosted streams are not modeled) |
| category | text | the department's product category |
| start_date | date | |
| end_date | date | |
| status | text | planned / running / finished |
| has_plan_doc | boolean | whether a campaign plan exists, default false |
| has_review_doc | boolean | whether a post-campaign review exists, default false |
| planned_operating_cost | numeric | planned operating cost (placement fees + promotion + giveaways, no product cost); known at planning time |
| actual_operating_cost | numeric | actual operating cost; only known once the campaign is over and the invoices are in, so it may be NULL |

**How ROI is computed** (the formula took two corrections; written down so it doesn't go wrong again):

```
ROI = SUM(campaign_products.profit) / campaigns.actual_operating_cost
```

- The numerator sums `campaign_products.profit`, which **already deducts product cost**
  (profit = sales - unit cost × units sold); product cost must not be deducted again in the denominator
- The denominator is **operating cost only** (the extra marketing spend of this campaign), without
  product cost. Product cost is what doing business costs anyway, not new spend for this campaign;
  ROI asks "was this marketing spend worth it", not "is the whole business worth it"
- Planned and actual cost are stored separately because invoices never match the budget exactly
  (the mock data makes them differ on purpose)
- The formula is written once, as the `roi` column of the `campaign_summary` view (V9), so a query never
  rebuilds it. It is a fixed formula over uploaded numbers, not analysis: why a campaign's ROI came out as it
  did stays in its review

`has_plan_doc` / `has_review_doc` are **a shortcut, not the source of truth**: the truth is whether
`documents` has a row with this `campaign_id` and `doc_type`. The flags only let the router answer
"there is no document at all, skip the search" with one cheap query before deciding on a vector
search. Cost: ingestion must update them whenever it inserts `documents` rows. The redundancy is
deliberate, not an oversight.

### campaign_metrics (daily campaign metrics)

Only clean numbers the system can get directly. Attribution and root-cause analysis need judgment and
**do not belong here**: they are content and go through the RAG pipeline of the `documents` table
(`campaign_review` documents). ROI is not stored either: the `campaign_summary` view computes it.

| Column | Type | Notes |
|---|---|---|
| id | bigserial PK | |
| campaign_id | bigint FK → campaigns | |
| date | date | |
| gmv | numeric | gross merchandise value |
| dau | int | |
| watch_time | numeric | average watch time (minutes) |
| conversion_rate | numeric | |
| peak_concurrent_viewers | int | peak concurrent viewers that day |
| avg_dwell_time_minutes | numeric | average minutes a viewer stayed that day |

> Department filtering: `campaign_metrics JOIN campaigns ON ... WHERE campaigns.department_id = :dept`

### campaign_products (per-product sales) -- uploaded by ops, not computed by us

| Column | Type | Notes |
|---|---|---|
| id | bigserial PK | |
| campaign_id | bigint FK → campaigns | |
| product_name | text | no separate product catalog table; product names are stored as text for now |
| units_sold | int | |
| sell_through_rate | numeric | |
| return_rate | numeric | |
| sales_amount | numeric | |
| click_through_rate | numeric | |
| cost_price | numeric | unit cost |
| profit | numeric | total profit of this product in this campaign |
| profit_margin | numeric | |

`cost_price` / `profit` / `profit_margin` exist because ROI can't be GMV minus a vague "cost": product
cost (`cost_price × units_sold`, summed) has to come off, and these columns make it a number a
structured query can fetch instead of an estimate in a review document. In reality they would come
with the uploaded table like the other columns (computed by the finance system), not be computed
and stored by us.

### campaign_customer_behavior (customer behavior) -- uploaded by ops, not computed by us

Audience conclusions such as repeat-purchase rate are **not ours to compute**: in reality another team
(growth / data platform) computes and syncs them. But this table stores **raw customer-campaign
behavior records**, not a precomputed repeat-purchase metric, so asking about repeat purchases later
is one plain SQL query (group by `customer_ref`, count the distinct campaigns) with no order or
transaction system behind it.

| Column | Type | Notes |
|---|---|---|
| id | bigserial PK | |
| campaign_id | bigint FK → campaigns | |
| customer_ref | text | anonymized customer ID, no real names or phone numbers; indexed to find one customer across campaigns |
| region | text | |
| dwell_time_minutes | numeric | how long this customer stayed in this livestream |
| products_purchased | jsonb | array of purchased products |
| purchase_amount | numeric | |
| ip_address | text | |

`campaign_products` / `campaign_customer_behavior` are **structured table uploads** (CSV exports from ops),
a different ingestion pipeline from `documents` (free text) below: the former are validated and loaded straight
into rows and queried with Text-to-SQL; the latter are chunked and embedded and retrieved with RAG. See
[architecture.md](architecture.md).

An upload replaces all of a campaign's rows in one transaction. A table has no `documents` row to point at its
current version: that is the last `done` upload of the campaign and table type. In a CSV the columns are the ones
above in any order, plus an optional `campaign_id` that must match the upload; `products_purchased` is
semicolon-separated (`Serum;Lipstick`). Samples are in `data_tables/`.

### The data dictionary and the metric views (V9)

Text-to-SQL is told what the data means by the database itself. Every business table and column has a
`COMMENT`: its meaning, its unit (US dollars, minutes, a fraction where 0.0874 is 8.74%) and what it is not
(planned cost has no product cost in it; the customer table is a sample). `app/sql_agent/schema.py` reads them
from the catalog at run time, so a model sees the definitions in the migrations, never a copy that drifts.

| View | One row per | What it saves a query from working out |
|---|---|---|
| `campaign_summary` | campaign | department and host names; GMV, conversion, viewers summed or averaged over its days; units, sales and profit over its products; `roi` |
| `campaign_daily_metrics` | campaign and day | the campaign's name, department and start date next to each day |
| `campaign_product_sales` | campaign and product | the same, next to each product |
| `campaign_customers` | campaign and sampled customer | the same, without `ip_address` |
| `host_profiles` | host | the host's department name |

- **Days and products are summed apart.** Joined together, each campaign's 20-odd daily rows would multiply
  its 3–6 product rows, and every sum would come out several times too large. Each is summed in its own
  subquery.
- **`avg_conversion_rate` is the mean of the daily rates**, each day counting the same. Averaging several
  campaigns' values weighs each campaign the same; averaging their daily rows weighs longer campaigns more.
  The column comment says so, because a question can mean either.
- **`security_invoker = true` on every view.** A view normally reads its tables as its owner, and the owner
  isn't subject to row-level security, so a view would bypass it. With this option the tables are read as
  whoever queries the view (PostgreSQL 15 and later).
- **No view has `ip_address`**, and the schema a model is shown leaves it out of the table too: personal data
  no question needs.
- Campaign and department are carried as names (`department = 'Beauty'`), the way questions name them.

## Document tables (SOP / campaign plan / post-campaign review)

The three document types share one table and differ by `doc_type`, because they go through **the same
ingestion pipeline** (upload -> Kafka -> parse -> chunk -> embed); three tables would mean three copies
of the ingestion logic.

| doc_type | When it is created | Content |
|---|---|---|
| `sop` | any time | operating procedures / guidelines; may be `ALL` (every department) |
| `campaign_plan` | uploaded before the campaign goes live | livestream plan, kept so AI can compare plans with results and improve future plans |
| `campaign_review` | uploaded after the campaign | post-campaign review -- **why the results came out as they did**: which choices worked, which products carried the campaign. The numbers themselves are in the tables |

### documents
| Column | Type | Notes |
|---|---|---|
| doc_id | bigserial PK | stays the same when a new version replaces the document (the row is updated in place and its chunks rebuilt), so citations and eval labels stay valid |
| upload_id | uuid unique FK → uploads | the upload that produced the current version |
| version | int | starts at 1, goes up by one with every replacement. For people and citations ("version 3"); replacements are checked against `upload_id`, not this |
| doc_type | text | `sop` / `campaign_plan` / `campaign_review` |
| title | text | e.g. "Account Restriction Handling" |
| department | text | department name or `ALL` (`sop` only) -- not a foreign key, see below |
| campaign_id | bigint FK → campaigns, nullable | only for `campaign_plan` / `campaign_review`; must be NULL for `sop` (enforced by a CHECK constraint) |
| category | text | e.g. "Trust & Safety", "Launch checklist", "Review" |
| steps | jsonb | structured steps, mainly for `sop`, see below |
| responsible_team | text | owning team |
| source_path | text | original file path |
| created_at | timestamptz | |

**Natural keys** (what makes two uploads "the same document"; a re-upload is checked against them to
decide between new and replacement):
- Plans / reviews: `(campaign_id, doc_type)` is unique, one of each per campaign. `campaign_id` is NULL
  for `sop`, and a unique constraint treats NULLs as distinct, so this constraint only covers campaign documents.
- SOP: titles are unique within a `department` (or `ALL`), compared after normalization (NFKC
  full-width to ASCII + trimmed + lowercase), so `" Account Restriction Handling "` and
  `"account restriction handling"` are the same document. The table keeps the original title.

`department` is TEXT, not a foreign key, so it can hold the sentinel `'ALL'`, which is not a real row in
`departments` (cost: renaming a department means updating this by hand; rare enough to accept).
For `campaign_plan` / `campaign_review` the `department` should equal the campaign's department.
The host of a document can be traced through `documents.campaign_id -> campaigns.host_id`, so
`documents` does not store a redundant `host_id`.

`steps` jsonb shape (so questions like "first step / last step / how many steps" can be answered without
a vector search). Filled from the parsed document by `sop_steps()` in `llm-service/app/rag/chunking.py`:
every top-level numbered list item, numbered within its list, with its nested items:
```json
[
  {"section": "Account Restriction Handling > Procedure", "order": 1,
   "text": "Identify the rule and the account: Log in to the platform's seller center ...", "substeps": []},
  {"section": "Account Restriction Handling > Procedure", "order": 7,
   "text": "Follow up on the result:",
   "substeps": ["Appeal approved: confirm every feature is restored ...", "Appeal rejected: ask the Trust & Safety contact ..."]}
]
```

Which values a row gets: `title` and `department` always come from the upload (form + the uploader's
identity), never from the file, because the department decides visibility and approval. A Markdown
frontmatter only fills `responsible_team`, and `category` when the form left it empty; a frontmatter that
disagrees is ignored with a warning (`llm-service/app/rag/document_fields.py`).

### document_catalog (view)

One row per document, computed from `documents` + `campaigns` + `hosts` (a view, not stored columns, so a
renamed campaign or host never leaves a stale copy behind):

| Column | Example |
|---|---|
| `display_name` | `SOP_All_Account Restriction Handling`, `Review_2025-11-18_Beauty_Tommy Walter_Beauty Black Friday Live` |
| `type_label` | `SOP` / `Campaign plan` / `Post-campaign review` |
| `department_label` | the department, or `All departments` for `ALL` |
| `campaign_name`, `start_date`, `host_name` | empty for SOPs |

The display name is for people (citations, chunk headers, later storage paths); retrieval filters on the
structured columns, never on a name. The labels are defined in one place, the SQL function
`catalog_entry(doc_type, title, department, campaign_id)` (V5), which the view calls for every row. Ingestion
calls the same function before the document row exists, to build the contextual chunk headers and embed the
chunks before opening the write transaction.

### doc_chunks (chunks + vectors)
| Column | Type | Notes |
|---|---|---|
| chunk_id | bigserial PK | |
| doc_id | bigint not null | together with `department`, a composite foreign key to documents |
| department | text | **deliberately redundant**: retrieval and RLS filter on this column without a JOIN |
| chunk_index | int | position of the chunk; `(doc_id, chunk_index)` is unique |
| content | text | contextual header + chunk text: exactly what was embedded (see "Chunking" in `llm-service/README.md`) |
| embedding | vector(1536) | dimension of the embedding model (text-embedding-3-small = 1536) |
| embedding_model | text | the model that produced the vector. Only tells apart models of the same dimension; a model with another dimension needs another column or table |
| metadata | jsonb | only what belongs to the chunk: `{"heading_path": [...], "pages": [...], "token_count": n}`. `doc_type`, `campaign_id` and the rest are joined from `documents` when needed, not copied onto every chunk |

The redundant `department` is kept correct by a **composite foreign key**: `(doc_id, department)`
references `documents(doc_id, department)`, so a chunk's department can only be its document's
department, and the database rejects anything else. `ON UPDATE CASCADE`: when a document changes
department, its chunks follow; `ON DELETE CASCADE`: deleting a document deletes its chunks.

Indexes: HNSW on `embedding` (`vector_cosine_ops`), a plain index on `department` (when retrieval for a
small department uses an exact scan, it filters by department first).

### uploads (upload records + processing status)
One row per upload. The backend writes `received` (or `pending_approval` when approval is needed) in the same
transaction as the `outbox` row carrying the event; the consumer updates the status while processing. Shared by
both pipelines (documents and data tables). Who moves the status:

| Transition | By | When |
|---|---|---|
| → `received` | backend | the upload is accepted; committed together with its outbox row, before anything is published |
| `received` / `failed` → `processing` | consumer | it starts working on the message (`failed` again after a replay from the dead-letter topic) |
| `pending_approval` → `received` / `rejected` | backend | the reviewer's decision; approving writes the outbox row in the same transaction |
| `processing` → `done` | consumer | in the same transaction that writes `documents` + `doc_chunks`, or the table rows |
| `processing` → `failed` | consumer | the message was dead-lettered; `error` holds the reason |
| `received` / `processing` → `failed` | backend | the reconciliation job gave up after publishing it again 3 times |

`done` is also the consumer's deduplication record: a message for an upload that is already `done` is skipped.

| Column | Type | Notes |
|---|---|---|
| upload_id | uuid PK | the same value as `uploadId` in the Kafka message, and the key the consumer deduplicates on |
| source | text | `manual` / `auto` |
| uploaded_by | bigint FK → users, nullable | required for manual uploads, NULL for automatic syncs (enforced by a CHECK constraint) |
| content_kind / doc_type / table_type | text | the same values and combination rules as the message contract |
| department | text | department name, or `ALL` (`sop` only) |
| campaign_id | bigint FK → campaigns, nullable | required for campaign documents and data tables |
| title | text | required for `sop` (part of its natural key); campaign document titles are generated |
| category | text, nullable | the category given at upload. A waiting upload has no event yet, so the row has to keep it |
| original_filename / storage_key | text | original file name / storage location (`storage_key` is unique) |
| replaces_upload_id | uuid FK → uploads, nullable | the upload that produced the current version this one replaces; NULL means new |
| status | text | `pending_approval` / `received` / `processing` / `done` / `failed` / `rejected` |
| error | text | failure reason |
| reviewed_by / reviewed_at / review_comment | | reviewer, review time, review comment. The reviewer can't be the uploader (enforced by a CHECK constraint) |
| created_at / updated_at | timestamptz | |
| delivery_count | int | deliveries that started without finishing, written before any work and reset on `done` or `failed`. In the database because it has to survive the process dying. Only the consumer writes it: the backend's entity deliberately doesn't map it |
| republish_count / last_republished_at | int / timestamptz | how often the reconciliation job published the event again, and when. See "Reconciliation" in [architecture.md](architecture.md) |

```
no approval needed:                      received -> processing -> done / failed
approval needed:  pending_approval --approve--> received -> processing -> done / failed
                                   --reject---> rejected
```

When the replacement actually runs, it checks again that the current version is still
`replaces_upload_id`; if not, someone else replaced it while the request waited for approval and the
request is void (optimistic concurrency).

> Replaces the earlier `doc_ingest_status`, whose primary key was `doc_id`: there is no `doc_id` before
> the document is written, and a data-table upload never produces a document, so it could record neither case.

### outbox (events waiting to be published)
The transactional outbox (V6). An upload's event is written here in the same transaction as the uploads row;
the backend's relay publishes it to Kafka afterwards and marks it. See "Transactional outbox" in
[architecture.md](architecture.md).

| Column | Type | Notes |
|---|---|---|
| id | bigserial PK | also the publishing order |
| topic | text | the Kafka topic |
| message_key | text | the Kafka key (the upload id) |
| payload | text | the message exactly as it is published (JSON) |
| created_at | timestamptz | |
| published_at | timestamptz, nullable | NULL until Kafka has acknowledged the message |
| attempts | int | failed publish attempts so far |
| last_error | text | why the last attempt failed |

A partial index on `id WHERE published_at IS NULL` keeps the relay's lookup as small as the backlog.
Published rows are deleted after 7 days. Something waiting: `SELECT * FROM outbox WHERE published_at IS NULL`.

## Mock data (generated)

The generator is reproducible: seeded `random` and Faker, every date computed from a fixed `AS_OF`
date (2026-10-15), so every run produces the same rows.

- 4 departments (Beauty / Consumer Electronics / Fresh Grocery / Home Goods)
- 25 users (4 managers, 19 staff, 1 executive, 1 admin; the last two belong to no department)
- 15 hosts (3-5 per department)
- 50 campaigns (5 seasonal ones per department -- Black Friday Live, Holiday Sale, Summer Sale, Back to
  School, Anniversary Sale, each starting inside its season -- plus 5-10 year-round ones), 15-45 days of
  metrics each, 1,462 metric rows; operating cost calibrated to 10%-20% of GMV
- 229 campaign_products rows (with unit cost / profit / margin; sales calibrated to stay below the
  campaign's GMV), 1,362 campaign_customer_behavior rows
- 5 SOPs (2 for `ALL` + 3 department-specific) in [sop_docs/](../sop_docs/)
- 1 campaign plan + post-campaign review pair (based on `campaign_id = 1`, "Beauty Black Friday Live";
  the numbers match the mock data, see [campaign_docs/README.md](../campaign_docs/README.md)) in
  [campaign_docs/](../campaign_docs/)
