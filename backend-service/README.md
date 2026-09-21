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

成功返回 `202 Accepted` + `{"uploadId": "..."}`。文件存到 `UPLOAD_DIR`(`.env` 里配,默认 `../uploads`,
即仓库根的 `uploads/`,gitignore 了不进仓库;llm-service 读的是同一个变量),
同时发一条 JSON 消息到 `document-upload-events`(topic 名从 `.env` 的 `DOC_UPLOAD_TOPIC` 读)。

**消息里是 `storageKey`(逻辑键 = 文件名),不是文件路径**——Day 6 最初发的是 `path`(相对本目录、
带 Windows 反斜杠),Day 8 消费者去读文件时才发现别的服务根本解析不了。契约的唯一事实来源是
[contracts/upload-event.examples.json](../contracts/upload-event.examples.json),`UploadEventContractTest`
和 Python 侧 `tests/test_events.py` 对着同一份文件验证。

**文件名清理**:`getOriginalFilename()` 是客户端随便填的(`../../evil.md`、Windows 风格的 `C:\x\y.md`、
控制字符、null),`sanitizeFilename` 把路径分隔符/Windows 非法字符/控制字符统一替换成 `_`,
得到任何操作系统上都只能是单层文件名的字符串;中文原样保留。

**注意是 `upload_id` 不是 `doc_id`**——`documents.doc_id` 是 Postgres 自增列,要等 Day 8+
消费者真正执行 `INSERT` 才会产生,上传这一刻还不存在,不可能提前塞进消息里。

`contentKind`/`docType`/`tableType`/`source` 在 Java 代码里是枚举(`upload/` 包下的
`ContentKind`/`DocType`/`TableType`/`Source`),不是裸字符串——防止手滑拼错值。
`@JsonValue` 保证序列化成 JSON 时还是这四张表约定好的小写字符串(`"sop"` 不是
`"SOP"`),枚举只让 Java 这一侧更安全,不改变线上的消息格式,也不会自动跟数据库
的 CHECK 约束或 Python 那边保持同步——这三处仍然要靠人记得同时改。

**失败语义**(Day 7 实测补的):
- 参数不合法 → `400` + 具体原因
- Kafka 不可用/topic 不存在 → `503`,约 5 秒内返回(producer 配了 `max.block.ms` 等超时),
  **已存盘的文件会被清理**,不留孤儿
- 等发送结果超时/被中断 → `503`,但**文件保留**——这种"结果不确定"的情况消息可能其实已经
  发出去了,此时删文件会造成"有消息没文件",比留个孤儿更糟

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

## 测试

```bash
.\mvnw.cmd test                                                # 默认:39 个单元/切片测试,不需要 Docker,几秒钟
.\mvnw.cmd test -DexcludedGroups=none -Dgroups=integration     # 集成测试:需要 Postgres 在跑
.\mvnw.cmd test -Dtest=UploadControllerTest                    # 只跑某个类
```

| 测试类 | 类型 | 内容 |
|---|---|---|
| `UploadEnumsTest`(13) | 纯单元 | 枚举的 `fromValue`(拼错/大小写/null)、`needsCampaign`、**JSON 线上格式必须小写**(`@JsonValue`) |
| `UploadControllerTest`(25) | `@WebMvcTest` 切片 | 成功路径(含实际发出去的 JSON)、10 种参数错误各自返回 400 + 具体原因 + **零副作用**、4 个 Kafka 故障回归测试、恶意文件名不能逃出上传目录、`sanitizeFilename` 的 7 种输入 |
| `UploadEventContractTest`(1) | 契约 | `contracts/upload-event.examples.json` 里每条样例经 Java 反序列化再序列化,必须与原文一致 |
| `BackendServiceApplicationTests`(1) | 集成,`@Tag("integration")` | 完整容器 + 真实 Postgres;因为 `ddl-auto: validate`,它同时检测 **Entity 是否跟 `02_schema.sql` 漂移** |

要点:
- `@WebMvcTest` 只加载 Web 层,Kafka / 两个 Repository 用 `@MockitoBean` 换成假的——这样才能**按需让 Kafka 出故障**(真 Kafka 做不到"恰好在这一刻抛某个异常")
- 上传目录和发送超时是配置项(`document.upload-dir` / `document.send-timeout-ms`),测试指向临时目录、把 15 秒改成 300 毫秒。**这是写测试"逼"出来的设计改进**:硬编码的路径和超时让代码没法测
- Spring Boot 4 的测试类换了包:`@WebMvcTest` 在 `org.springframework.boot.webmvc.test.autoconfigure`,`@MockBean` 已被 `@MockitoBean` 取代,JUnit 是 6.x——别照抄 Boot 3 时代的教程
- **变异验证过**:故意把 5 个 bug 改回去(catch 错异常类型 / 不清理孤儿文件 / 不恢复中断标志 / 超时也删文件 / 误删 `@JsonValue`),每个都被对应的测试抓到变红。没见过它失败的测试,不能证明它有用

## 计划中的接口

| 接口 | 天 | 说明 |
|---|---|---|
| `GET /actuator/health` | 4 | 健康检查 |
| `POST /documents/upload` | 6 ✅ | 上传文件 → 存盘 → 发 Kafka `document-upload-events` |
| `POST /query` | 13 | 转发给 llm-service /query(带 `X-User-Id`) |
| 结构化数据只读接口 | 5+ | campaigns / campaign_metrics / anchors 查询(给前端看板用,可选) |
