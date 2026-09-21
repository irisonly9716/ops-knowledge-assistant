package com.opsassistant.backend.upload;

import static org.assertj.core.api.Assertions.assertThat;

import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * 契约测试:backend-service(Java,生产者)和 llm-service(Python,消费者)之间的
 * Kafka 消息格式,唯一的事实来源是 contracts/upload-event.examples.json。
 *
 * 两边的测试都对着【同一份文件】验证:
 *   - 这里:文件里每条样例,反序列化成 UploadEvent 再序列化回去,必须跟原文一字不差
 *     (字段名、枚举的小写写法、null 的显式出现)——保证 Java 发出去的就是契约规定的样子
 *   - Python 侧(llm-service/tests/test_events.py):同一批样例必须能被 pydantic 模型解析
 * 任何一边悄悄改了格式(改字段名、枚举值大小写),这份文件对不上,对应那一边的测试就红。
 * 这比"各写各的、靠人记得同步"可靠——Day 6 的 path 字段就是这样悄悄成了坑。
 */
class UploadEventContractTest {

    // Maven/surefire 的工作目录是 backend-service/,契约文件在仓库根目录的 contracts/
    private static final Path CONTRACT = Path.of("..", "contracts", "upload-event.examples.json");

    private final JsonMapper mapper = JsonMapper.builder().build();

    @Test
    void every_contract_example_survives_a_java_round_trip_unchanged() throws Exception {
        JsonNode examples = mapper.readTree(Files.readString(CONTRACT));
        assertThat(examples.size()).as("契约文件里至少要有样例").isGreaterThanOrEqualTo(3);

        for (JsonNode example : examples) {
            UploadEvent event = mapper.readValue(example.toString(), UploadEvent.class);
            JsonNode reserialized = mapper.readTree(mapper.writeValueAsString(event));

            assertThat(reserialized)
                    .as("样例 %s 经 Java 往返后应与契约原文一致", example.get("uploadId"))
                    .isEqualTo(example);
        }
    }
}
