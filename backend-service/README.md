# backend-service

Spring Boot / Java. Public REST API, file uploads, structured business tables (JPA), Kafka producer.

The project skeleton was generated with the Spring Initializr settings below (calling start.spring.io
with `curl`). What came back was **Spring Boot 4.1.1** (newer than the 3.3.x/3.4.x listed below:
start.spring.io's default version keeps moving forward, which doesn't affect the starters we use).

## Generating with Spring Initializr

Open https://start.spring.io/, or `New Project → Spring Boot` in IntelliJ IDEA, and choose:

| Setting | Value |
|---|---|
| Project | Maven |
| Language | Java |
| Spring Boot | the default stable version (3.3.x / 3.4.x) |
| Group | `com.opsassistant` |
| Artifact | `backend-service` |
| Package name | `com.opsassistant.backend` |
| Packaging | Jar |
| Java | 21 when generated, **now 25** (see the Java version note below) |

Dependencies:
- Spring Web
- Spring Data JPA
- PostgreSQL Driver
- Spring for Apache Kafka
- Spring Boot Actuator (a ready-made health check at `/actuator/health`)
- Lombok (optional, fewer getters/setters to write)

Unzip the result and copy its `pom.xml` / `src/` / `mvnw*` into this folder (the one this README is in).

### Downloading from the command line (optional, instead of the web page)

```bash
curl https://start.spring.io/starter.zip \
  -d type=maven-project -d language=java -d javaVersion=21 \
  -d groupId=com.opsassistant -d artifactId=backend-service \
  -d packageName=com.opsassistant.backend -d name=backend-service \
  -d dependencies=web,data-jpa,postgresql,kafka,actuator,lombok \
  -o backend-service.zip
# unzip into the current folder
```

## Java version

The machine runs **Java 25**, and the project's compile target (`<java.version>` in `pom.xml`) is **25** too.

The project was generated with 21 (JDK 25 builds a 21 target fine; the Maven build always worked).
**It was changed to 25** because of the editor, not the build: the VS Code Java extension uses the
Eclipse compiler with its bundled Lombok (1.18.39), which at language level 21 rejects Lombok-generated
constructors (`@AllArgsConstructor` and friends) with
`Flexible Constructor Bodies is only available with source level 25`. Files show red, and it also writes
broken classes into `target/classes`; Maven sees them as newer than the sources, doesn't recompile, and
startup fails with `ClassFormatError`. At language level 25 the feature is allowed and the problem is gone.
The cost: Docker / deployment needs a Java 25 base image. (The 21 in the Initializr table above is the
option used to generate the project; `pom.xml` is authoritative now.)

## Running it

```bash
cd backend-service
.\mvnw.cmd spring-boot:run      # WSL/macOS/Linux: ./mvnw spring-boot:run
```

- The datasource settings in `application.yml` come from `.env` at the repository root (host/port/database
  have defaults, **the password has none and must be set in `.env`**); start Postgres/Kafka first with `docker compose up -d`
- `http://localhost:8081/health` → `{"status":"ok","service":"backend-service"}`
- `http://localhost:8081/actuator/health` → `{"status":"UP",...}`

## Jackson version

Spring Boot 4 uses **Jackson 3** by default: both the Maven coordinates and the Java packages moved from
`com.fasterxml.jackson.*` to **`tools.jackson.*`**. Most tutorials and StackOverflow answers still show
Jackson 2 (`com.fasterxml.jackson.databind.ObjectMapper`), and copying them doesn't compile.
`JacksonException` also changed from Jackson 2's checked exception (`extends IOException`) to an unchecked one.

## Implemented endpoints

### `POST /documents/upload`

Implements the "two ingestion pipelines share one topic" design in
[docs/architecture.md](../docs/architecture.md). `multipart/form-data` with an `X-User-Id` header naming the
uploader (a `user_id` from `users`; missing, not a number or unknown → `401`, checked before anything else).
The rules are in [docs/permission-model.md](../docs/permission-model.md): staff and managers upload, executive and
admin never do (`403`). Fields:

| Field | Required | Notes |
|---|---|---|
| `file` | yes | the file to upload, at most 20 MB. Documents: `.md` `.markdown` `.docx` `.pdf`. Data tables: `.csv`. Anything else is a `400` |
| `contentKind` | yes | `document` (SOP / plan / review, goes through RAG) or `data_table` (product / customer behavior table, stored as rows) |
| `docType` | when `contentKind=document` | `sop` / `campaign_plan` / `campaign_review` |
| `tableType` | when `contentKind=data_table` | `campaign_products` / `campaign_customer_behavior` |
| `mode` | yes | `new` or `replace`. The uploader says which, and the system checks it against what exists instead of guessing |
| `replacesDocId` | when replacing a document | the document to replace. A data table is identified by campaign + table type, so it doesn't need one |
| `campaignId` | for everything except `sop` | `campaign_plan` / `campaign_review` / `data_table` all belong to a campaign, which must be in the uploader's department |
| `title` | `sop` only | an SOP is identified by department + title; plan and review titles are generated from the campaign, so sending one is a `400` |
| `scope` | `sop` only | `department` (the uploader's own) or `all` (every department) |
| `category` | no | |

There is no `department` field: it follows from the uploader, so a client can't upload into a department by
naming it.

A file over the limit gets a `413`. A client that sends the whole body before reading the response only sees that
answer if Tomcat reads the rest of the refused request first, hence `server.tomcat.max-swallow-size`: measured, the
default of 2 MB dropped the connection for such a client at 21 MB, while 100 MB gave a `413` at 21 and 90 MB. Far above
that (measured at 150 MB) the connection is still dropped, so one client can't make the server read without limit.

| Answer | When |
|---|---|
| `202`, `{"uploadId": "...", "status": "received"}` | a new document or table for the own department: takes effect without review |
| `202`, `{"uploadId": "...", "status": "pending_approval"}` | replacing anything, or anything for `all`: waits for the uploader's superior; the old version stays in effect |
| `400` | malformed: a missing or unknown field, a combination that makes no sense, the wrong file type, an unknown campaign |
| `403` | not this uploader's to touch: executive / admin, a campaign or document of another department |
| `409` | the request is fine but doesn't fit what exists: `new` and it exists, `replace` and it doesn't, a new title another SOP has |

The file is stored in `UPLOAD_DIR` (set in `.env`, default `../uploads`, i.e. `uploads/` at the repository root,
gitignored; llm-service reads the same variable), and **one transaction writes the `uploads` row and, unless it
waits for approval, an `outbox` row holding the JSON event** (`UploadService`). The request returns once that
commits; it never talks to Kafka. If the transaction fails, neither row exists, the file is removed and the
response is `500`.

### `GET /documents/{uploadId}/status`

How far an upload got: `status`, `error` (the reason for `failed`, e.g. `row 3, units_sold: 'abc' is not a whole
number`), what it is, who reviewed it. Visible to the uploader, whoever may review it, and admin; anyone else gets
`403`, an unknown id `404`.

### Approval: `GET /documents/approvals`, `POST /documents/approvals/{uploadId}/approve` and `/reject`

`GET` lists what the current user has to decide: a manager sees the staff of their own department, the executive
sees managers, anyone else gets `403`. Both decisions take an optional `comment`.

- **approve**: the upload becomes `received` and its event goes into the outbox, in one transaction
- **reject**: `rejected`; nothing was published, so the current version is untouched, and the file is deleted
- A request can wait for days, and locking a document all that time is out of the question. So **approving checks
  whether the version it wants to replace is still the current one** (optimistic concurrency). If someone else's
  version went live meanwhile, approving would overwrite their work: the answer is `409`, the upload is `rejected`
  with `superseded: ...` as its error, and its file is deleted. The transaction still commits, so the decision is on
  record
- `403`: not the uploader's superior. `409`: already decided. `404`: unknown upload

### `GET /campaigns?month=2026-10`

Campaigns running at some point in a month (one that spans two months is in both). A plan or review is bound to a
campaign by its id, and uploaders know "the sale in November", not an id. `hasPlanDoc` / `hasReviewDoc` show whether
one exists, which decides between `mode=new` and `mode=replace`. Staff and managers see their own department;
executive and admin belong to none and see all. A malformed month is a `400`.

### `POST /query`

Body: `{"question": "..."}`, at most 2,000 characters; `X-User-Id` says who is asking. The role and department come from
the `users` table, so anything else in the body is ignored. `QueryClient` forwards them to llm-service with the internal
token (`INTERNAL_SERVICE_TOKEN` in `.env`; both services read it).

```json
{
  "status": "answered",
  "answer": "Do this [1].",
  "citations": [{"number": 1, "docId": 3, "docType": "sop", "title": "Account Restriction Handling",
                 "department": "ALL", "headingPath": ["Account Restriction Handling", "Steps"], "score": 0.5183}],
  "searchedDepartments": ["Beauty", "ALL"]
}
```

`status` is `refused` when the documents this user may see don't answer the question: `200` with no citations.
`searchedDepartments` is `null` for executive and admin, who search everything.

| Answer | When |
|---|---|
| `400` | empty or too long, or the body isn't JSON |
| `401` | unknown user. Never because of the internal token: a token the two services disagree on is a `502` and an error in the log, since the client can do nothing about it |
| `502` | the model failed, or its answer failed validation (for example it cited an excerpt that doesn't exist) |
| `503` | llm-service is down or not configured, the search is unavailable, or the provider is rate limiting |
| `504` | no answer in time: llm-service's own budget of 12 s, or the read timeout of 14 s here if it is stuck |

`spring.jpa.open-in-view` is off. Its default holds a database connection until the response is written, which here
means through the whole model call. Measured with llm-service taking 2 s: 40 queries at once took 8.3 s, in batches the
size of the connection pool of 10. With it off, 2.3 s.

### Outbox relay

`OutboxRelay` publishes the outbox to `document-upload-events` (the topic name comes from `DOC_UPLOAD_TOPIC` in
`.env`). Every second (`OutboxScheduler`) it locks the oldest unpublished rows (`FOR UPDATE SKIP LOCKED`),
publishes them in order, waits for Kafka's acknowledgement and marks each published. A failed or unacknowledged
send is recorded on the row (`attempts`, `last_error`) and retried later; while Kafka stays down the scheduler
backs off (2 s, 4 s, 8 s ... up to 30 s). Published rows are deleted after 7 days. Settings: `outbox.*` in
`application.yml`.

Why: recording the upload and publishing its event are two systems with no shared transaction. Done one after
the other, a crash in between leaves an upload recorded but never published (the "dual write"). Written into the
same database transaction, the upload and its event can't be separated, and publishing can be retried for as
long as it takes. A message can be published twice (the relay dies after Kafka acknowledged but before marking
the row); the consumer is idempotent, so the copy is skipped.

**The message carries `storageKey` (a logical key = the file name), not a file path**: a path relative to
this folder, with Windows backslashes, can't be resolved by another service. The single source of truth for
the contract is [contracts/upload-event.examples.json](../contracts/upload-event.examples.json);
`UploadEventContractTest` and the Python side's `tests/test_events.py` validate against the same file.

**File name sanitizing**: `getOriginalFilename()` is whatever the client sent (`../../evil.md`, a
Windows-style `C:\x\y.md`, control characters, null). `sanitizeFilename` replaces path separators,
characters Windows forbids and control characters with `_`, giving a string that can only be a
single-level file name on any operating system; ordinary characters, including non-ASCII letters, are kept.

**It is `upload_id`, not `doc_id`**: `documents.doc_id` is a Postgres identity column that only exists
once the consumer runs the `INSERT`. At upload time it doesn't exist yet, so it can't go into the message.

`contentKind` / `docType` / `tableType` / `source` are enums in the Java code (`ContentKind` / `DocType` /
`TableType` / `Source` in the `upload/` package), not raw strings, so a value can't be misspelled.
`@JsonValue` keeps the JSON in the agreed lowercase strings (`"sop"`, not `"SOP"`). The enums only make the
Java side safer: they don't change the wire format, and they don't sync themselves with the database CHECK
constraints or the Python side -- those three places still have to be changed together (the contract and
drift tests catch it when they aren't).

**Failure semantics** (all verified):
- invalid parameters → `400` + the specific reason
- unknown or missing `X-User-Id` → `401`, before any parameter is looked at
- the database can't record the upload → `500`, nothing is recorded or published, the file is removed
- **Kafka unavailable → the upload is still accepted (`202`)**; its event waits in the outbox and is published
  once Kafka is back. Measured end to end: Kafka stopped, upload accepted in 0.3 s, three publish attempts
  recorded with backoff, the backend restarted meanwhile, Kafka started again -- the relay published the event
  and the consumer ingested the document with no manual step
- a send that fails, times out or is interrupted is a retry, never a lost message; a duplicate it may cause is
  skipped by the consumer

`campaignId` is **not** an enum: it is business data (a new campaign can appear any day), not a fixed set of
values baked into the schema. It is checked against the real table instead (`CampaignRepository.findById`);
an unknown one gets 400.

Check it:
```bash
curl -X POST http://localhost:8081/documents/upload -H "X-User-Id: 2" \
  -F "file=@some.md" -F "contentKind=document" -F "docType=sop" -F "mode=new" -F "scope=department" -F "title=Some SOP"
```
Then ask for its status with `curl http://localhost:8081/documents/<uploadId>/status -H "X-User-Id: 2"` (with the
consumer running it moves on to `done`), or read the message with `llm-service/scripts/kafka_consumer_test.py`.

## Tests

```bash
.\mvnw.cmd test                                                # default: 187 unit/slice tests, no Docker needed, a few seconds
.\mvnw.cmd test -DexcludedGroups=none -Dgroups=integration     # integration tests: need the docker compose Postgres
.\mvnw.cmd test -Dtest=UploadControllerTest                    # one class only
```

| Test class | Kind | Covers |
|---|---|---|
| `UploadEnumsTest` (13) | plain unit | the enums' `fromValue` (typos / case / null), `needsCampaign`, **the JSON wire format must be lowercase** (`@JsonValue`) |
| `UploadPolicyTest` (15) | plain unit | the permission matrix of `docs/permission-model.md`, row by row: who uploads, what needs approval, who reviews whom, who sees a status |
| `UploadControllerTest` (75) | `@WebMvcTest` slice + the real `UploadService` | takes effect at once / waits for approval, every `403` and `409`, 25 malformed requests each returning 400 + the reason + **no side effects**, missing / unknown `X-User-Id` → 401, a failed recording removes the file, malicious file names, the status endpoint's visibility |
| `ApprovalControllerTest` (28) | `@WebMvcTest` slice + the real `UploadService` | the queues, approve writes the event, reject removes the file, **stale requests of every kind are void**, only the uploader's superior decides, a decision is made once |
| `CampaignControllerTest` (10) | `@WebMvcTest` slice | own department or all, which days a month means, malformed months |
| `QueryControllerTest` (29) | `@WebMvcTest` slice + the real `QueryClient` over a real socket to a stub llm-service | the role and department come from the users table and can't be chosen, the token is sent, every failure maps to its status with no internal details, read timeout, service down |
| `QueryContractTest` (2) | contract | every example in `contracts/query.examples.json` round-trips exactly in Java |
| `UploadServiceTest` (2) | plain unit | the outbox row: topic, key, the event in the lowercase wire format; a waiting upload has no event |
| `OutboxRelayTest` (7) | plain unit | publishing in order and marking; every Kafka failure (send throws Kafka's own `TimeoutException`, failed future, no acknowledgement in time, interruption) is recorded, stops the batch and is retried later |
| `OutboxSchedulerTest` (5) | plain unit | draining a backlog, exponential backoff capped at the maximum, reset after success, retention cleanup |
| `UploadEventContractTest` (1) | contract | every example in `contracts/upload-event.examples.json`, deserialized and re-serialized by Java, must equal the original |
| `OutboxIntegrationTest` (4) | integration, `@Tag("integration")` | real Postgres: **the upload and its event commit together, and a failed outbox write leaves no upload behind**; the relay marks rows; a row locked by another relay is skipped, not waited for |
| `UploadFlowIntegrationTest` (12) | integration, `@Tag("integration")` | whole requests against real Postgres: what the slices fake. The SOP natural key (case, spaces, Unicode forms, per department), the approval queues, what each decision commits, a void approval surviving as a commit with a `409` answer, data table versions, the campaign search |
| `QueryConnectionIntegrationTest` (1) | integration, `@Tag("integration")` | waiting for llm-service holds no database connection: 6 queries at once on a pool of 2 |
| `BackendServiceApplicationTests` (1) | integration, `@Tag("integration")` | the full container + a real Postgres; with `ddl-auto: validate` it also detects **drift between the entities and the migrations in `db/migration/`** |

Notes:
- `@WebMvcTest` loads only the web layer. The upload tests add the real `UploadService` on top and fake only the
  repositories via `@MockitoBean`, because the rules live in the service: a status code is tested through the code
  that decides it. The relay tests use a Mockito `KafkaTemplate`, which is what lets them **make Kafka fail on
  demand** (a real Kafka can't be made to throw a particular exception at a particular moment)
- The integration tests run against the development database. They only touch rows they create and delete them
  afterwards, and `OutboxIntegrationTest` refuses to run while real messages wait in the outbox (its relay would
  mark them published without really sending them). The scheduler is switched off in tests
  (`outbox.relay.enabled=false`); they call the relay directly
- The upload folder and the relay's send timeout are settings, so tests point at a temporary folder and cut
  15 seconds to 300 ms. Hard-coded paths and timeouts would have made the code untestable
- Spring Boot 4 moved the test classes: `@WebMvcTest` is in
  `org.springframework.boot.webmvc.test.autoconfigure`, `@MockBean` is replaced by `@MockitoBean`, and JUnit
  is 6.x -- don't copy Boot 3 era tutorials
- **Mutation-tested**: bugs were put back on purpose and each one turned its test red -- among them catching
  the wrong exception type, not restoring the interrupt flag, deleting `@JsonValue`, and for the outbox:
  removing `@Transactional` from `UploadService.record`, `FOR UPDATE` without `SKIP LOCKED`, a batch that
  continues after a failure, a message not marked published, no backoff; for the upload rules, every line of the
  policy, every check in the service, the queries' filters. A test you have never seen fail proves nothing

## Endpoint overview

| Endpoint | Status | Notes |
|---|---|---|
| `GET /health`, `GET /actuator/health` | implemented | health checks |
| `POST /documents/upload` | implemented | upload file → check the rules → store → record the upload and, unless it waits for approval, its event (outbox) → the relay publishes to Kafka `document-upload-events` |
| `GET /documents/{id}/status` | implemented | how far an upload got, and why it failed |
| `GET /documents/approvals`, `POST /documents/approvals/{id}/approve`, `.../reject` | implemented | the approval queue and the decision |
| `GET /campaigns?month=` | implemented | finding a campaign to upload a plan, review or table for |
| `POST /query` | implemented | looks up the user, forwards to llm-service with the internal token, returns the answer with its citations or a refusal |
| read-only structured data endpoints | planned | campaigns / campaign_metrics / hosts queries (for a frontend dashboard, optional) |
