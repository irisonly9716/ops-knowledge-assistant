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
- Known trade-off: there is only one `executive`, so every manager's request queues up for the CEO alone.
  A real company would usually delegate to vice presidents.

### Known limitations

- Identity is simulated: anyone can forge the `X-User-Id` header. Real authentication (JWT and the like)
  is designed but not implemented. The accurate statement is: the authorization rules are real and
  enforced by the database; authentication is simulated.
- Separation of duties governs actions taken through the system. A developer connected directly to the
  database can still change anything; a real environment constrains that with separate database accounts
  (migration, application and read-only accounts) plus operation auditing.

## 2. Filtering data (Text-to-SQL)

- Every business table (`campaigns` / `hosts` / `campaign_metrics` via a join) has `department_id`
- After the LLM generates SQL, code **enforces the filter before execution** instead of trusting the LLM:
  - `role in (staff, manager)` → `WHERE department_id = :current_dept` (or `AND` into an existing WHERE)
  - `role in (executive, admin)` → no filter
- The SQL safety limits stay: only `SELECT`; `DROP` / `DELETE` / `UPDATE` / `INSERT` are rejected
- Implementation: parse the AST with an SQL parser (such as `sqlglot`) to check for a pure SELECT and
  inject the department predicate; sturdier than string concatenation

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
- [ ] "The last campaign" only resolves among campaigns the user can see: a newer campaign in another department changes nothing, and a follow-up question never lists one
- [ ] Asking about another department's campaign id gets the same answer as an id that doesn't exist

## 6. No permissions at the host level for now

`campaigns.host_id` / `documents.campaign_id -> campaigns.host_id` only **trace** which host a campaign,
plan or review belongs to; they are not a permission dimension. The required scale (8 departments, 500
people, so about 60 per department) suggests a host-team level in the org chart. Finer permissions such as
"team leads see only their team" were considered and left out: department-level permissions come first
and are validated end to end; the team level can come later and is out of scope for now.
