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
Fields:

| Field | Required | Notes |
|---|---|---|
| `file` | yes | the file to upload |
| `contentKind` | yes | `document` (SOP / plan / review, goes through RAG) or `data_table` (product / customer behavior table, stored as rows) |
| `docType` | when `contentKind=document` | `sop` / `campaign_plan` / `campaign_review` |
| `tableType` | when `contentKind=data_table` | `campaign_products` / `campaign_customer_behavior` |
| `department` | yes | department name, or `ALL` (`sop` only) |
| `campaignId` | for everything except `sop` | `campaign_plan` / `campaign_review` / `data_table` all belong to a campaign |
| `title` | `sop` only | an SOP is identified by department + title; plan and review titles are generated from the campaign, so sending one is a `400` |
| `category` | no | |

Success returns `202 Accepted` + `{"uploadId": "..."}`. The file is stored in `UPLOAD_DIR` (set in `.env`,
default `../uploads`, i.e. `uploads/` at the repository root, gitignored; llm-service reads the same
variable), and **one transaction writes the `uploads` row (status `received`) and an `outbox` row holding the
JSON event** (`UploadService`). The request returns once that commits; it never talks to Kafka. If the
transaction fails, neither row exists, the file is removed and the response is `500`.

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

`department` / `campaignId` are **not** enums: they are business data (a new department or campaign can
appear any day), not a fixed set of values baked into the schema. They are checked against the real
tables instead (`DepartmentRepository.existsByName` / `CampaignRepository.existsById`); unknown values get
400. `ALL` is the sop-only sentinel, not a row in `departments`, and is let through.

Check it:
```bash
curl -X POST http://localhost:8081/documents/upload -H "X-User-Id: 2" \
  -F "file=@some.md" -F "contentKind=document" -F "docType=sop" -F "department=ALL" -F "title=Some SOP"
```
Then check the row with `SELECT status, error FROM uploads ORDER BY created_at DESC LIMIT 1;` (with the
consumer running it moves on to `done`), or read the message with `llm-service/scripts/kafka_consumer_test.py`.

## Tests

```bash
.\mvnw.cmd test                                                # default: 55 unit/slice tests, no Docker needed, a few seconds
.\mvnw.cmd test -DexcludedGroups=none -Dgroups=integration     # integration tests: need the docker compose Postgres
.\mvnw.cmd test -Dtest=UploadControllerTest                    # one class only
```

| Test class | Kind | Covers |
|---|---|---|
| `UploadEnumsTest` (13) | plain unit | the enums' `fromValue` (typos / case / null), `needsCampaign`, **the JSON wire format must be lowercase** (`@JsonValue`) |
| `UploadControllerTest` (28) | `@WebMvcTest` slice | success paths (the uploads row and the event handed to `UploadService`), missing / unknown `X-User-Id` → 401, 13 kinds of invalid parameters each returning 400 + the reason + **no side effects**, a failed recording removes the file, malicious file names can't escape the upload folder, 7 inputs to `sanitizeFilename` |
| `UploadServiceTest` (1) | plain unit | the outbox row: topic, key, the event in the lowercase wire format |
| `OutboxRelayTest` (7) | plain unit | publishing in order and marking; every Kafka failure (send throws Kafka's own `TimeoutException`, failed future, no acknowledgement in time, interruption) is recorded, stops the batch and is retried later |
| `OutboxSchedulerTest` (5) | plain unit | draining a backlog, exponential backoff capped at the maximum, reset after success, retention cleanup |
| `UploadEventContractTest` (1) | contract | every example in `contracts/upload-event.examples.json`, deserialized and re-serialized by Java, must equal the original |
| `OutboxIntegrationTest` (4) | integration, `@Tag("integration")` | real Postgres: **the upload and its event commit together, and a failed outbox write leaves no upload behind**; the relay marks rows; a row locked by another relay is skipped, not waited for |
| `BackendServiceApplicationTests` (1) | integration, `@Tag("integration")` | the full container + a real Postgres; with `ddl-auto: validate` it also detects **drift between the entities and the migrations in `db/migration/`** |

Notes:
- `@WebMvcTest` loads only the web layer, with the repositories and `UploadService` replaced by fakes via
  `@MockitoBean`. The relay tests use a Mockito `KafkaTemplate`, which is what lets them **make Kafka fail on
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
  continues after a failure, a message not marked published, no backoff. A test you have never seen fail
  proves nothing

## Endpoint overview

| Endpoint | Status | Notes |
|---|---|---|
| `GET /health`, `GET /actuator/health` | implemented | health checks |
| `POST /documents/upload` | implemented | upload file → store → record the upload and its event (outbox) → the relay publishes to Kafka `document-upload-events` |
| `POST /query` | planned | forwards to llm-service /query (with `X-User-Id`) |
| read-only structured data endpoints | planned | campaigns / campaign_metrics / hosts queries (for a frontend dashboard, optional) |
