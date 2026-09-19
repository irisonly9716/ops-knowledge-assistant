package com.opsassistant.backend.upload;

import static java.nio.charset.StandardCharsets.UTF_8;
import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.multipart;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.opsassistant.backend.campaign.CampaignRepository;
import com.opsassistant.backend.department.DepartmentRepository;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;
import java.util.stream.Stream;
import org.apache.kafka.clients.producer.RecordMetadata;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.MethodSource;
import org.mockito.ArgumentCaptor;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.support.SendResult;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import tools.jackson.databind.json.JsonMapper;

/**
 * UploadController 的接口测试。@WebMvcTest 只加载 Web 层(Controller、请求解析、
 * 异常处理、JSON 序列化),不启动整个应用——所以不需要 Postgres/Kafka 在跑,
 * 依赖的三个外部组件用 @MockitoBean 换成"假的",这样才能【按我们的要求让 Kafka 出故障】,
 * 这是真的 Kafka 做不到的(你没法可靠地让它恰好在这一刻抛某个异常)。
 *
 * 分组:成功路径(消息内容对不对) / 参数校验(该拒的都拒了、且什么副作用都没有) /
 * Kafka 故障路径(Day 7 实测发现三个 bug 的地方,每个 bug 对应一个回归测试)。
 */
@WebMvcTest(UploadController.class)
class UploadControllerTest {

    /** 上传的文件写到这个临时目录,不碰真实的 backend-service/uploads。 */
    @TempDir
    static Path uploadDir;

    @DynamicPropertySource
    static void properties(DynamicPropertyRegistry registry) {
        registry.add("document.upload-dir", () -> uploadDir.toString());
        registry.add("document.upload-topic", () -> "test-topic");
        // 生产是 15 秒;测"结果不确定"分支要真等一次超时,改成 300 毫秒
        registry.add("document.send-timeout-ms", () -> "300");
    }

    @Autowired
    MockMvc mockMvc;

    @MockitoBean
    KafkaTemplate<String, String> kafkaTemplate;
    @MockitoBean
    DepartmentRepository departmentRepository;
    @MockitoBean
    CampaignRepository campaignRepository;

    private final JsonMapper json = JsonMapper.builder().build();

    @BeforeEach
    void setUp() throws IOException {
        try (Stream<Path> files = Files.list(uploadDir)) {
            files.forEach(p -> p.toFile().delete()); // 每个测试从空目录开始
        }
        // 只有这个部门、这个 campaign 在"库"里存在;其它一律 Mockito 默认的 false
        when(departmentRepository.existsByName("美妆运营")).thenReturn(true);
        when(campaignRepository.existsById(3L)).thenReturn(true);
    }

    // ------------------------------------------------------------------ 成功路径

    @Test
    void sop_upload_is_accepted_and_published_message_uses_the_lowercase_wire_format() throws Exception {
        kafkaSendSucceeds();

        MvcResult result = mockMvc.perform(request(Map.of(
                        "contentKind", "document", "docType", "sop", "department", "ALL")))
                .andExpect(status().isAccepted())
                .andReturn();
        Map<String, Object> response = readMap(body(result));
        String uploadId = (String) response.get("uploadId");
        assertThat(response).containsEntry("status", "accepted");

        ArgumentCaptor<String> key = ArgumentCaptor.forClass(String.class);
        ArgumentCaptor<String> value = ArgumentCaptor.forClass(String.class);
        verify(kafkaTemplate).send(eq("test-topic"), key.capture(), value.capture());

        assertThat(key.getValue()).isEqualTo(uploadId); // 用 uploadId 当 Kafka key
        Map<String, Object> message = readMap(value.getValue());
        assertThat(message)
                .containsEntry("uploadId", uploadId)
                .containsEntry("contentKind", "document") // 小写,不是 DOCUMENT
                .containsEntry("docType", "sop")
                .containsEntry("department", "ALL")
                .containsEntry("source", "manual");
        assertThat(message.get("campaignId")).isNull();
        assertThat(uploadDir.resolve(uploadId + "-note.md")).exists(); // 文件真的存了
    }

    @Test
    void campaign_review_upload_carries_the_campaign_id() throws Exception {
        kafkaSendSucceeds();

        mockMvc.perform(request(Map.of("contentKind", "document", "docType", "campaign_review",
                        "department", "美妆运营", "campaignId", "3")))
                .andExpect(status().isAccepted());

        ArgumentCaptor<String> value = ArgumentCaptor.forClass(String.class);
        verify(kafkaTemplate).send(eq("test-topic"), anyString(), value.capture());
        assertThat(readMap(value.getValue()))
                .containsEntry("docType", "campaign_review")
                .containsEntry("campaignId", 3)
                .containsEntry("department", "美妆运营");
    }

    @Test
    void data_table_upload_uses_tableType_instead_of_docType() throws Exception {
        kafkaSendSucceeds();

        mockMvc.perform(request(Map.of("contentKind", "data_table", "tableType", "campaign_products",
                        "department", "美妆运营", "campaignId", "3")))
                .andExpect(status().isAccepted());

        ArgumentCaptor<String> value = ArgumentCaptor.forClass(String.class);
        verify(kafkaTemplate).send(eq("test-topic"), anyString(), value.capture());
        Map<String, Object> message = readMap(value.getValue());
        assertThat(message)
                .containsEntry("contentKind", "data_table")
                .containsEntry("tableType", "campaign_products");
        assertThat(message.get("docType")).isNull();
    }

    // ------------------------------------------------------------------ 参数校验

    record BadCase(String name, Map<String, String> params, String expectedFragment) {
        @Override
        public String toString() {
            return name; // 测试报告里显示这个名字,一眼看出哪个场景挂了
        }
    }

    static Stream<BadCase> badRequests() {
        return Stream.of(
                new BadCase("document 没传 docType",
                        Map.of("contentKind", "document", "department", "美妆运营"), "docType"),
                new BadCase("docType 拼错(campain_plan)",
                        Map.of("contentKind", "document", "docType", "campain_plan",
                                "department", "美妆运营", "campaignId", "3"), "campain_plan"),
                new BadCase("contentKind 非法(video)",
                        Map.of("contentKind", "video", "department", "美妆运营"), "video"),
                new BadCase("campaign_review 没带 campaignId",
                        Map.of("contentKind", "document", "docType", "campaign_review",
                                "department", "美妆运营"), "必须带 campaignId"),
                new BadCase("sop 却带了 campaignId",
                        Map.of("contentKind", "document", "docType", "sop",
                                "department", "美妆运营", "campaignId", "3"), "不能带 campaignId"),
                new BadCase("data_table 没传 tableType",
                        Map.of("contentKind", "data_table", "department", "美妆运营",
                                "campaignId", "3"), "tableType"),
                new BadCase("tableType 拼错(少个 s)",
                        Map.of("contentKind", "data_table", "tableType", "campaign_product",
                                "department", "美妆运营", "campaignId", "3"), "campaign_product"),
                new BadCase("data_table 没带 campaignId",
                        Map.of("contentKind", "data_table", "tableType", "campaign_products",
                                "department", "美妆运营"), "必须带 campaignId"),
                new BadCase("部门不存在(美妆运运)",
                        Map.of("contentKind", "document", "docType", "sop", "department", "美妆运运"),
                        "department 不存在"),
                new BadCase("campaignId 不存在(99999)",
                        Map.of("contentKind", "document", "docType", "campaign_review",
                                "department", "美妆运营", "campaignId", "99999"), "campaignId 不存在"));
    }

    @ParameterizedTest(name = "{0}")
    @MethodSource("badRequests")
    void invalid_requests_get_400_with_a_specific_reason_and_no_side_effects(BadCase bad) throws Exception {
        MvcResult result = mockMvc.perform(request(bad.params()))
                .andExpect(status().isBadRequest())
                .andReturn();

        assertThat((String) readMap(body(result)).get("error")).contains(bad.expectedFragment());
        verifyNoInteractions(kafkaTemplate); // 被拒的请求不能往 Kafka 发任何东西
        assertThat(fileCount()).isZero();    // 也不能在磁盘上留下任何文件
    }

    // ------------------------------------------------------------------ Kafka 故障路径

    /**
     * 回归测试:Day 7 抓到的 bug ②。topic 不存在时 KafkaTemplate.send() 【自己】抛出
     * org.apache.kafka.common.errors.TimeoutException(不是返回一个失败的 Future)。
     * 当时代码 catch 的是 java.util.concurrent.TimeoutException——同名不同包,
     * 异常漏过去,变成 Spring 默认的笼统 500,且遗留孤儿文件(bug ③)。
     */
    @Test
    void send_throwing_kafkas_own_TimeoutException_gives_503_and_cleans_up_the_file() throws Exception {
        when(kafkaTemplate.send(anyString(), anyString(), anyString()))
                .thenThrow(new org.apache.kafka.common.errors.TimeoutException(
                        "Topic test-topic not present in metadata after 5000 ms."));

        MvcResult result = mockMvc.perform(request(sopParams()))
                .andExpect(status().isServiceUnavailable())
                .andReturn();

        assertThat((String) readMap(body(result)).get("error")).contains("暂不可用");
        assertThat(fileCount()).isZero(); // 确定没发出去 -> 孤儿文件必须被清理
    }

    @Test
    void future_that_fails_gives_503_and_cleans_up_the_file() throws Exception {
        when(kafkaTemplate.send(anyString(), anyString(), anyString()))
                .thenReturn(CompletableFuture.failedFuture(new RuntimeException("broker down")));

        mockMvc.perform(request(sopParams()))
                .andExpect(status().isServiceUnavailable());

        assertThat(fileCount()).isZero(); // Future 以异常结束 = 确定失败 -> 清理
    }

    /**
     * "超时 ≠ 失败":等发送结果超时了,消息【可能已经发出去了】,消费者随后会来读这个文件。
     * 这时删文件会造成"有消息、没文件",比留一个孤儿更糟——所以必须保留。
     */
    @Test
    void timeout_waiting_for_the_send_result_is_ambiguous_so_the_file_is_KEPT() throws Exception {
        when(kafkaTemplate.send(anyString(), anyString(), anyString()))
                .thenReturn(new CompletableFuture<>()); // 永远不完成

        MvcResult result = mockMvc.perform(request(sopParams()))
                .andExpect(status().isServiceUnavailable())
                .andReturn();

        assertThat((String) readMap(body(result)).get("error")).contains("不确定");
        assertThat(fileCount()).isEqualTo(1); // 结果不确定 -> 不能删
    }

    /**
     * 线程被中断:文件保留,并且【中断标志必须被恢复】。InterruptedException 抛出时 JVM 已经
     * 把线程的中断标志清掉了,如果 catch 里直接吞掉,"有人要求这个线程停下"的信号就丢了。
     */
    @Test
    @SuppressWarnings("unchecked")
    void interruption_is_ambiguous_keeps_the_file_and_restores_the_interrupt_flag() throws Exception {
        CompletableFuture<SendResult<String, String>> future = mock(CompletableFuture.class);
        when(future.get(anyLong(), eq(TimeUnit.MILLISECONDS))).thenThrow(new InterruptedException("test"));
        when(kafkaTemplate.send(anyString(), anyString(), anyString())).thenReturn(future);

        try {
            mockMvc.perform(request(sopParams())).andExpect(status().isServiceUnavailable());

            assertThat(fileCount()).isEqualTo(1);
            assertThat(Thread.currentThread().isInterrupted())
                    .as("catch InterruptedException 之后必须调用 Thread.currentThread().interrupt() 恢复标志")
                    .isTrue();
        } finally {
            Thread.interrupted(); // 清掉标志,别影响后面的测试
        }
    }

    // ------------------------------------------------------------------ 辅助方法

    private static Map<String, String> sopParams() {
        return Map.of("contentKind", "document", "docType", "sop", "department", "ALL");
    }

    private static org.springframework.test.web.servlet.request.MockMultipartHttpServletRequestBuilder request(
            Map<String, String> params) {
        var builder = multipart("/documents/upload")
                .file(new MockMultipartFile("file", "note.md", "text/markdown", "# 内容".getBytes(UTF_8)));
        params.forEach(builder::param);
        return builder;
    }

    /** MockHttpServletResponse 默认按 ISO-8859-1 解码,中文错误信息必须显式指定 UTF-8。 */
    private static String body(MvcResult result) throws Exception {
        return result.getResponse().getContentAsString(UTF_8);
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> readMap(String text) {
        return json.readValue(text, Map.class);
    }

    private long fileCount() throws IOException {
        try (Stream<Path> files = Files.list(uploadDir)) {
            return files.count();
        }
    }

    /**
     * 顺序很重要:先把 future 完整构造好,再去 stub kafkaTemplate。
     * 不能写成 when(kafkaTemplate.send(...)).thenReturn(succeededFuture())——
     * succeededFuture() 内部自己也在 when(...) stub 别的 mock,而 Mockito 不允许在
     * 一个 stubbing 还没写完时嵌套另一个(UnfinishedStubbingException)。
     */
    private void kafkaSendSucceeds() {
        CompletableFuture<SendResult<String, String>> future = succeededFuture();
        when(kafkaTemplate.send(anyString(), anyString(), anyString())).thenReturn(future);
    }

    @SuppressWarnings("unchecked")
    private static CompletableFuture<SendResult<String, String>> succeededFuture() {
        RecordMetadata metadata = mock(RecordMetadata.class);
        when(metadata.partition()).thenReturn(0);
        when(metadata.offset()).thenReturn(7L);
        SendResult<String, String> result = mock(SendResult.class);
        when(result.getRecordMetadata()).thenReturn(metadata);
        return CompletableFuture.completedFuture(result);
    }
}
