# Department permission model

> Permission checks can be wired in late, but the `department` field and the `users` table must be
> **built into the schema from the start**; otherwise the vector table, Text-to-SQL and retrieval
> filters all need rework. This document pins down the rules first.

## 1. Who is asking -- user identity

The project has no login module and uses the lightest possible approach:

- a `users` table: `user_id, name, department_id, role`
- requests carry an `X-User-Id` header; the backend looks up `department_id` and `role` and forwards them to llm-service
- upgrading to a simple JWT later would demonstrate real authentication, but that is not the focus now

**llm-service only trusts the backend.** It receives the role and department in the request and believes them, so
anyone who could reach it could claim to be an executive and read everything, which is worse than forging
`X-User-Id`. So `/query` requires the shared `INTERNAL_SERVICE_TOKEN` (no token, wrong token or an unset one: `401`), it
refuses to start without one, and it listens on 127.0.0.1 by default. **When deploying, never expose port 8000**: keep it on a private
network or behind a firewall where only the backend can reach it.

### Roles

| role | Who | Department |
|---|---|---|
| `staff` | ops employee | required |
| `manager` | department manager, one per department | required |
| `executive` | founder / CEO | none (must be NULL) |
| `admin` | system administrator, i.e. whoever runs this system | none (must be NULL) |

"Required / must be NULL" is enforced by a CHECK constraint on `users`.

### What each role can see

| role | Documents | Business data | System data (audit log, all upload records and failure reasons) |
|---|---|---|---|
| `staff` | own department + `ALL` | own department | no |
| `manager` | own department + `ALL` | own department | no |
| `executive` | everything | everything | no: the audit log holds every question every employee asked, which is private |
| `admin` | everything | everything | yes |

Cross-department access by `executive` and `admin` is flagged separately in the audit table.

### What each role can change -- separation of duties

**Documents and data tables are business content: only business staff upload them, and their superior
approves.** Whoever runs the system (`admin`) doesn't touch business content; whoever writes content
can't approve it.

| Action | staff | manager | executive | admin |
|---|---|---|---|---|
| Create a document / data table for their own department | takes effect | takes effect | no | no |
| Replace their own department's document / data table | department manager approves | executive approves | no | no |
| Create or replace an `ALL` document | department manager approves | executive approves | no | no |
| Approve | no | requests from their department's staff | requests from any manager | no |
| System maintenance (replay dead letters, re-embed after a model change, run evals) | no | no | no | yes |

The rule in one sentence: **when approval is needed, the uploader's superior approves, and the approver
must be able to see the content.** Other department managers can't see a department-only document, so a
manager's request can only go to the `executive`, who sees every department.

- System maintenance doesn't count as "changing content": it changes no document content and no approval decision.
- While a request awaits approval the old version stays in effect and is replaced only after approval; a
  rejected upload's new file is deleted.
- "The approver is not the uploader" is enforced by a CHECK constraint on `uploads`; "the approver is the
  uploader's superior" needs a cross-table comparison, is checked in application code and covered by
  privilege-escalation tests.
- Every cell of the table above is a case of `PermissionMatrixTest` (backend), through the HTTP endpoints: each role
  against each action, approving one's own request and another department's included.
- Known trade-off: there is only one `executive`, so every manager's request queues up for the CEO alone.
  A real company would usually delegate to vice presidents.

### Known limitations

- Identity is simulated: anyone can forge the `X-User-Id` header. Real authentication (JWT and the like)
  is designed but not implemented. The accurate statement is: the authorization rules are real and
  enforced by the database; authentication is simulated.
- Separation of duties governs actions taken through the system. A developer connected directly to the
  database can still change anything. Generated SQL already runs as an account of its own (`sql_reader`, below);
  migrations, the backend and the consumers still share `ops`, which a real environment would split, with
  operation auditing.

## 2. Filtering data (Text-to-SQL)

- Every business table (`campaigns` / `hosts` / `campaign_metrics` via a join) has `department_id`, and every
  metric view a generated query may read has the department's name
- After the LLM generates SQL, code **enforces the filter before execution** instead of trusting the LLM
  (`app/sql_agent/guard.py`, implemented):
  - `role in (staff, manager)` → every view the query reads, wherever it appears (joins, subqueries, `WITH`,
    `UNION`), is replaced by `(SELECT * FROM view WHERE department = current_setting('app.department'))` under
    the name the query uses for it. Adding a `WHERE` to the outer query instead would miss a view read in a subquery
  - the department is set for the transaction with `SET LOCAL app.department = ...`, never written into the query.
    A query can't change it: the function allow-list has neither `set_config` nor `current_setting`, and the database
    refuses `set_config` (below). Without the setting a filtered query reads no department: an error, or no rows on a
    pooled connection that had it before
  - `role in (executive, admin)` → no filter in the query; the transaction sets `app.all_departments = 'on'` for the
    database layer below
- The checks before that, by parsing the query with `sqlglot` (allow-lists, because a list of dangerous things is never
  complete): one `SELECT`; only the five metric views, never a table, a system catalog or a schema-qualified name; a `WITH`
  query can't take a view's name or make a real table look like one of its own; only allow-listed functions (`pg_sleep`,
  `dblink`, `lo_import` and the like are refused) and casts to plain types (not `regclass` or `oid`); no writing, also
  inside `WITH`, no `SELECT INTO`, no `FOR UPDATE`. What runs is regenerated from the checked tree
- At most 201 rows: the outer query gets `LIMIT 201` unless its own limit is smaller (one more than the 200 kept, so a
  result still reads as cut off). In the query, so Postgres stops there: psycopg receives a whole result before handing
  over any of it, and 2 million rows took 2.0 s and 156 MB to keep 200, against 0.2 s with the limit

The database holds the department too (migration V11), so a query that gets past the checks still sees one department:

- **The model's query runs as `sql_reader`**: not a superuser, no `BYPASSRLS`, owns nothing, and may `SELECT` the five
  views and nothing else, so no table and no `ip_address`. Read-only and 5 s by default, which the code sets anyway.
  Only the model's query: the pipeline's own reads (the catalog, the names in the data, the examples) are fixed SQL with
  bound parameters and run as `ops`, like the rest of the query path
- **Row-level security on the six tables behind the views** reads the same settings. `departments` keeps the asker's
  department, or every one when `app.all_departments` is `'on'`; `hosts` and `campaigns` keep the rows of the
  departments left, the detail tables those of the campaigns left, so the department rule is written once. Nothing set,
  or a setting an earlier transaction left empty on a pooled connection: no rows
- **The views belong to `sql_views`** (no login, no `BYPASSRLS`, owns no table) and read their tables as it. A view
  normally reads its tables as its owner: owned by `ops` it would bypass row-level security; with V9's
  `security_invoker` it read them as whoever queried it, and a role granted only the views was refused the tables.
  The policies read the settings of the asker's transaction, whichever role reads the tables
- **`set_config` is revoked.** It is a function, so a `SELECT` can call it: `SELECT set_config('app.department',
  'Fresh Grocery', true), (SELECT count(*) FROM campaign_summary)` returned another department's rows, measured before
  revoking. `SET LOCAL` still sets it: a statement can never be part of a `SELECT`. `SET` takes no bound parameter, so
  the department is composed into it with psycopg's `sql.Literal`, which quotes it the way Postgres reads it
- **Each layer covers the other**: if the query filter misses a view, row-level security keeps the department; if a
  policy is wrong, the filter does. `ops` owns the tables, so it reads them unfiltered (the backend, the consumers);
  through the views it is filtered like everyone
- The database believes the setting of the connection: every asker shares one login through the pool. Only the code
  runs `SET`, before the query, and the query can't; whoever holds `sql_reader`'s password can choose any department,
  like any database password. One login per department would close that, at the price of a pool per department in
  every process against the 100 connections Postgres allows
- **Each layer was tried alone** (`llm-service/tests/test_sql_leaks.py`), against 19 queries written to get another
  department's data: filtering by another department's name, `OR 1 = 1`, `UNION`, `WITH`, subqueries, `LATERAL`, quoted
  and schema-qualified names, a table, the statistics catalog, `set_config`, a function running a query, a second
  statement, `ip_address`. Through the whole pipeline, staff and managers got none of another department's rows, and
  executives and admins were refused the same queries the checks refuse.
  The code alone (row-level security letting every row through) stopped all 19. The database alone (the query as
  written, as `sql_reader`) stopped 18: a second statement can `SET` another department, because `sql_reader` has to
  be able to set its own. Two checks in the code keep a query to one statement: `guard.py` parses exactly one and
  regenerates the query from it, and `run_select` refuses a semicolon
- **A question about another department alone is refused** before any query, by the query writer's instructions
  (`NOT_IN_DATA`). Answered on the views, which hold only the asker's rows, it returned the asker's own numbers, and the
  explanation could name them after the other department: not a leak, but misleading. A comparison with other
  departments still gets the asker's own numbers. The layers above, not this rule, keep the data apart
- **The statistics catalog** is a known way around row-level security: the planner samples a column's values into
  `pg_stats`. Postgres shows a column's statistics only to a role that may read the table and isn't held by
  row-level security on it, so `sql_reader` sees none of the business tables'
- **"The last campaign" is found the same way** (`app/sql_agent/campaigns.py`): a query written in code, with bound
  parameters, run as `sql_reader` under row-level security in the asker's department, with the department in its WHERE
  clause as well; tests take each layer away in turn. Another department's campaign is not found, exactly like one that
  doesn't exist, and a question asked back lists only campaigns this search found. The campaign list never goes to a
  model. Whether a campaign has a review is read as `ops`, by id, only for campaigns the search already returned

## 3. Filtering documents (vector search)

- `doc_chunks.department` is a real column (a department or `ALL`), kept consistent with its document by a composite foreign key
- Retrieval filters on this column:
  - `role in (staff, manager)` → `department IN (:current_dept, 'ALL')`
  - `role in (executive, admin)` → no filter
- An SOP carries `department` from ingestion on (in the upload event); company-wide procedures use `ALL`
- **⚠️ Vector search + a WHERE filter can silently return too few results (measured)**: HNSW is an
  approximate index. It first takes a batch of candidates by vector similarity (`hnsw.ef_search`,
  default 40) and **then** applies `WHERE department = ...`. If the target department holds 1% of the
  data, those 40 candidates very likely contain none of its rows. Measured with 20,000 rows / 100
  departments, asking for "department 7's top 10": the default settings return **0 rows** (no error);
  with `SET hnsw.iterative_scan = relaxed_order` (pgvector ≥ 0.8; this project runs 0.8.6) it returns 10,
  and an exact scan (no index) also returns 10. For permission filtering this is not just lower recall:
  **documents the user is allowed to see don't show up**. Used: **`SET LOCAL hnsw.iterative_scan = relaxed_order`**
  (`app/rag/retrieval.py`), with the results sorted again because this mode can return them slightly out of order. The
  planner still chooses an exact scan by itself when the filter keeps few rows. `test_retrieval.py` forces the index and
  shows both sides: 0 rows for a small department without the setting, all of them with it. Not chosen:
  - always scanning exactly, without the index: correct and fast enough at today's size, but it wouldn't hold at scale
  - partitions or partial indexes per department: for much larger data, and they need changes whenever a department is added
- A refusal reads the same whether a document exists in another department or not: anything else would tell people
  what other departments have
- `campaign_plan` / `campaign_review` documents follow the same filter (their `department` equals their
  campaign's, never `ALL`); they can also be narrowed down to "only this campaign's review" by
  `campaign_id` / `doc_type` through a join with `documents`

## 4. Campaigns that span departments

A campaign may involve several departments. Two options; the simple one first:

| Option | Description | Trade-off |
|---|---|---|
| **A. One owning department** (used now) | `campaigns.department_id` records only the owning department | simple; cross-department visibility comes from SOPs saying "work with department X" |
| B. Many-to-many | a `campaign_departments(campaign_id, department_id)` join table | more realistic, but SQL filtering and the schema get a level more complex |

## 5. Acceptance checklist

- [ ] A `staff` user asking for another department's campaign GMV gets no data / a clear "no permission"
- [ ] The same question from an `executive` or `admin` user gets the data
- [ ] A `manager`, like `staff`, gets no data from other departments
- [ ] Uploads and replacements by `executive` and `admin` are rejected; `admin` can't approve; nobody approves their own upload
- [ ] Document retrieval: a `staff` user can't retrieve another department's SOP but can retrieve company-wide (`ALL`) SOPs
- [ ] The SQL the LLM generates is filtered by `department_id` even when the question doesn't mention a department
- [x] "The last campaign" only resolves among campaigns the user can see: a newer campaign in another department changes nothing, and a question asked back never lists one (`tests/test_campaigns.py`)
- [x] Asking about another department's campaign gets the same answer as one that doesn't exist (`tests/test_campaigns.py`, and end to end through the backend)

## 6. No permissions at the host level for now

`campaigns.host_id` / `documents.campaign_id -> campaigns.host_id` only **trace** which host a campaign,
plan or review belongs to; they are not a permission dimension. The required scale (8 departments, 500
people, so about 60 per department) suggests a host-team level in the org chart. Finer permissions such as
"team leads see only their team" were considered and left out: department-level permissions come first
and are validated end to end; the team level can come later and is out of scope for now.
