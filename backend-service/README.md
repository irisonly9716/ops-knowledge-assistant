# backend-service

Spring Boot / Java。对外 REST API、文件上传、结构化业务表(JPA)、Kafka producer。

> **Day 4 才正式创建这个项目**(用 Spring Initializr 生成)。这里先放生成配置,
> 这样目录结构完整,Day 4 直接把生成的内容拷进来即可。

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

## 跑起来(Day 4)

```bash
cd backend-service
./mvnw spring-boot:run          # Windows: .\mvnw.cmd spring-boot:run
```

- `application.yml` 配数据源指向 `localhost:5432 / ops / ops_dev_pw / ops_assistant`
- 访问 `http://localhost:8081/actuator/health` → `{"status":"UP"}`

## 计划中的接口

| 接口 | 天 | 说明 |
|---|---|---|
| `GET /actuator/health` | 4 | 健康检查 |
| `POST /documents/upload` | 6 | 上传文件 → 存盘 → 发 Kafka `document-upload-events` |
| `POST /query` | 13 | 转发给 llm-service /query(带 `X-User-Id`) |
| 结构化数据只读接口 | 5+ | campaigns / campaign_metrics / anchors 查询(给前端看板用,可选) |
