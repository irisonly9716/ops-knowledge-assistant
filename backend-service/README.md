# backend-service

Spring Boot / Java。对外 REST API、文件上传、结构化业务表(JPA)、Kafka producer。

Day 4 已生成(用下面的 Spring Initializr 配置,通过 `curl` 直接调 start.spring.io)。
实际拿到的是 **Spring Boot 4.1.1**(比下面写的 3.3.x/3.4.x 新——start.spring.io 默认版本
一直在往前走,不影响我们用的这几个 starter)。

## 用 Spring Initializr 生成

打开 https://start.spring.io/ ,或 IntelliJ IDEA 里 `New Project → Spring Boot`,按下面选:

| 项 | 值 |
|---|---|
| Project | Maven |
| Language | Java |
| Spring Boot | 选默认的稳定版(3.3.x / 3.4.x) |
| Group | `com.opsassistant` |
| Artifact | `backend-service` |
| Package name | `com.opsassistant.backend` |
| Packaging | Jar |
| Java | **21**(见下方 Java 版本说明) |

Dependencies:
- Spring Web
- Spring Data JPA
- PostgreSQL Driver
- Spring for Apache Kafka
- Spring Boot Actuator(顺便,`/actuator/health` 现成的健康检查)
- Lombok(可选,少写 getter/setter)

生成后解压,把里面的 `pom.xml` / `src/` / `mvnw*` 拷到本目录(覆盖这个 README 所在的文件夹)。

### 命令行下载(可选,替代网页操作)

```bash
curl https://start.spring.io/starter.zip \
  -d type=maven-project -d language=java -d javaVersion=21 \
  -d groupId=com.opsassistant -d artifactId=backend-service \
  -d packageName=com.opsassistant.backend -d name=backend-service \
  -d dependencies=web,data-jpa,postgresql,kafka,actuator,lombok \
  -o backend-service.zip
# 解压到当前目录
```

## Java 版本说明

你本机是 **Java 25**。Spring Initializr 里选 **Java 21** 作为编译目标(`<java.version>21</java.version>`)——
用 JDK 25 的 JVM 跑一个以 21 为目标的构建没问题。
如果 Day 4 真遇到 Spring Boot 对 Java 25 报兼容问题,再单独装一个 JDK 21 切过去即可
(大纲原本就要求 JDK 21)。

## 跑起来

```bash
cd backend-service
.\mvnw.cmd spring-boot:run      # WSL/macOS/Linux: ./mvnw spring-boot:run
```

- `application.yml` 数据源默认指向 `localhost:5432 / ops / ops_dev_pw / ops_assistant`(跟 `.env.example` 对齐),要先 `docker compose up -d` 把 Postgres/Kafka 起起来
- 访问 `http://localhost:8081/health` → `{"status":"ok","service":"backend-service"}`
- 访问 `http://localhost:8081/actuator/health` → `{"status":"UP",...}`

## Jackson 版本注意

Spring Boot 4 默认用 **Jackson 3**,Maven 坐标和 Java 包名都从 `com.fasterxml.jackson.*`
换成了 **`tools.jackson.*`**——网上大部分教程/StackOverflow 答案还是 Jackson 2 的写法
(`com.fasterxml.jackson.databind.ObjectMapper`),照抄会编译不过。`JacksonException` 也从
Jackson 2 的受检异常(`extends IOException`)变成了非受检异常。

## 已实现的接口

### `POST /documents/upload`

对应 [docs/architecture.md](../docs/architecture.md) 的"两条摄入管道共用一个 topic"设计。
`multipart/form-data`,字段:

| 字段 | 必填 | 说明 |
|---|---|---|
| `file` | 是 | 要上传的文件 |
| `contentKind` | 是 | `document`(SOP/策划案/复盘,走 RAG)或 `data_table`(商品表/顾客行为表,直接落表) |
| `docType` | `contentKind=document` 时必填 | `sop` / `campaign_plan` / `campaign_review` |
| `tableType` | `contentKind=data_table` 时必填 | `campaign_products` / `campaign_customer_behavior` |
| `department` | 是 | 部门名或 `ALL`(仅 `sop` 可用) |
| `campaignId` | 除 `sop` 外必填 | `campaign_plan`/`campaign_review`/`data_table` 都要挂一个 campaign |
| `category` | 否 | |

成功返回 `202 Accepted` + `{"uploadId": "..."}`。文件存到 `uploads/`(gitignore 了,不进仓库),
同时发一条 JSON 消息到 `document-upload-events`(topic 名从 `.env` 的 `DOC_UPLOAD_TOPIC` 读)。

**注意是 `upload_id` 不是 `doc_id`**——`documents.doc_id` 是 Postgres 自增列,要等 Day 8+
消费者真正执行 `INSERT` 才会产生,上传这一刻还不存在,不可能提前塞进消息里。

`contentKind`/`docType`/`tableType`/`source` 在 Java 代码里是枚举(`upload/` 包下的
`ContentKind`/`DocType`/`TableType`/`Source`),不是裸字符串——防止手滑拼错值。
`@JsonValue` 保证序列化成 JSON 时还是这四张表约定好的小写字符串(`"sop"` 不是
`"SOP"`),枚举只让 Java 这一侧更安全,不改变线上的消息格式,也不会自动跟数据库
的 CHECK 约束或 Python 那边保持同步——这三处仍然要靠人记得同时改。

`department`/`campaignId` **不是**枚举——它们是业务数据(哪天可能多一个新部门/新
campaign),不是写死在 schema 里的固定几个值。这两个改成查真实的表验证存在性
(`DepartmentRepository.existsByName` / `CampaignRepository.existsById`),不存在就
400。`ALL` 是 sop 专用的哨兵值,不在 `departments` 表里,单独放行。

验证:
```bash
curl -X POST http://localhost:8081/documents/upload \
  -F "file=@some.md" -F "contentKind=document" -F "docType=sop" -F "department=ALL"
```
然后用 `llm-service/scripts/kafka_consumer_test.py` 或 `kafka-console-consumer` 确认收到。

## 计划中的接口

| 接口 | 天 | 说明 |
|---|---|---|
| `GET /actuator/health` | 4 | 健康检查 |
| `POST /documents/upload` | 6 ✅ | 上传文件 → 存盘 → 发 Kafka `document-upload-events` |
| `POST /query` | 13 | 转发给 llm-service /query(带 `X-User-Id`) |
| 结构化数据只读接口 | 5+ | campaigns / campaign_metrics / anchors 查询(给前端看板用,可选) |
