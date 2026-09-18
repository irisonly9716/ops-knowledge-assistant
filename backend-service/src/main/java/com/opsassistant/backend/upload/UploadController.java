package com.opsassistant.backend.upload;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.function.Supplier;
import com.opsassistant.backend.campaign.CampaignRepository;
import com.opsassistant.backend.department.DepartmentRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.support.SendResult;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.server.ResponseStatusException;
import tools.jackson.core.JacksonException;
import tools.jackson.databind.ObjectMapper;

/**
 * Day 6:上传接口 + Kafka 生产者。对应 docs/architecture.md 里"两条摄入管道共用一个
 * topic"的设计——文档类(SOP/策划案/复盘)和结构化表格类(商品表/顾客行为表)
 * 都走这一个接口,靠 contentKind 字段告诉 llm-service 消费者该怎么处理。
 *
 * 请求参数(contentKind/docType/tableType)进来时还是裸字符串——HTTP 表单字段
 * 本来就只能是字符串,类型系统帮不上忙——但一进方法体就立刻转换成枚举
 * (parseOrBadRequest),后面的业务逻辑全程用枚举,不再碰裸字符串。
 */
@Slf4j
@RestController
@RequestMapping("/documents")
@RequiredArgsConstructor
public class UploadController {

    private static final Path UPLOAD_DIR = Path.of("uploads");
    private static final String ALL_DEPARTMENTS = "ALL"; // sop 文档专用的哨兵值,不在 departments 表里

    private final KafkaTemplate<String, String> kafkaTemplate;
    private final ObjectMapper objectMapper; // Spring Boot 自动配置好的 Jackson 实例,直接注入用
    private final DepartmentRepository departmentRepository;
    private final CampaignRepository campaignRepository;

    @Value("${document.upload-topic}")
    private String uploadTopic;

    @PostMapping("/upload")
    public ResponseEntity<Map<String, String>> upload(
            @RequestParam("file") MultipartFile file,
            @RequestParam("contentKind") String contentKindRaw,
            @RequestParam(value = "docType", required = false) String docTypeRaw,
            @RequestParam(value = "tableType", required = false) String tableTypeRaw,
            @RequestParam("department") String department,
            @RequestParam(value = "campaignId", required = false) Long campaignId,
            @RequestParam(value = "category", required = false) String category
    ) throws IOException {
        ContentKind contentKind = parseOrBadRequest(() -> ContentKind.fromValue(contentKindRaw));
        DocType docType = null;
        TableType tableType = null;

        switch (contentKind) {
            case DOCUMENT -> {
                docType = parseOrBadRequest(() -> DocType.fromValue(docTypeRaw));
                if (docType.needsCampaign() && campaignId == null) {
                    throw badRequest("docType=" + docType.value() + " 必须带 campaignId");
                }
                if (!docType.needsCampaign() && campaignId != null) {
                    throw badRequest("docType=sop 不能带 campaignId");
                }
            }
            case DATA_TABLE -> {
                tableType = parseOrBadRequest(() -> TableType.fromValue(tableTypeRaw));
                if (campaignId == null) {
                    throw badRequest("contentKind=data_table 必须带 campaignId");
                }
            }
        }

        // department/campaignId 不是像 doc_type 那样"写死在 schema 里的固定几个值",
        // 是业务数据(哪天可能多一个新部门、多一场新 campaign),所以不用枚举——
        // 但也不能完全不管,得去查真实的表,确认传进来的值确实存在。
        // "ALL" 是 sop 专用的哨兵值,不在 departments 表里,单独放行。
        if (!ALL_DEPARTMENTS.equals(department) && !departmentRepository.existsByName(department)) {
            throw badRequest("department 不存在:" + department);
        }
        if (campaignId != null && !campaignRepository.existsById(campaignId)) {
            throw badRequest("campaignId 不存在:" + campaignId);
        }

        String uploadId = UUID.randomUUID().toString();
        Path dest = saveFile(file, uploadId);

        UploadEvent event = new UploadEvent(
                uploadId, dest.toString(), contentKind, docType, tableType,
                department, campaignId, category, Source.MANUAL
        );
        sendToKafka(uploadId, event);

        log.info("上传成功 uploadId={} contentKind={} 已发到 topic {}", uploadId, contentKind.value(), uploadTopic);
        return ResponseEntity.accepted().body(Map.of("uploadId", uploadId, "status", "accepted"));
    }

    /**
     * 各枚举的 fromValue() 匹配不上会抛 IllegalArgumentException(见 ContentKind 等类)。
     * 这里统一接住,转成带 400 状态码的 ResponseStatusException——三处调用
     * (contentKind/docType/tableType)共用这一段,不用把 try/catch 写三遍。
     *
     * Supplier<T> 是 Java 的一个函数式接口,代表"一个不接收参数、返回 T 类型值的动作"。
     * 传进来的 () -> ContentKind.fromValue(contentKindRaw) 就是这样一个"动作",
     * 调用 parser.get() 才会真正执行它——这样能把"可能抛异常的调用"整个包起来传进来。
     */
    private static <T> T parseOrBadRequest(Supplier<T> parser) {
        try {
            return parser.get();
        } catch (IllegalArgumentException e) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, e.getMessage());
        }
    }

    private ResponseStatusException badRequest(String message) {
        return new ResponseStatusException(HttpStatus.BAD_REQUEST, message);
    }

    private Path saveFile(MultipartFile file, String uploadId) throws IOException {
        Files.createDirectories(UPLOAD_DIR);
        // 文件名前面加 uploadId,防止两个人传同名文件互相覆盖
        String filename = uploadId + "-" + file.getOriginalFilename();
        Path dest = UPLOAD_DIR.resolve(filename);
        file.transferTo(dest);
        return dest;
    }

    private void sendToKafka(String uploadId, UploadEvent event) {
        // 注意包名是 tools.jackson.*,不是网上大部分教程里的 com.fasterxml.jackson.*——
        // Spring Boot 4 默认换成了 Jackson 3(连 Maven 坐标的 groupId 都变了)。
        // 但 @JsonValue/@JsonCreator 这些注解还留在老包名 com.fasterxml.jackson.annotation.*,
        // 两套包名混着用是 Jackson 3 真实的样子,不是我们写错了。
        // JacksonException 是运行时异常(不强制 try/catch),这里还是手动 catch 一下,
        // 是为了把错误包成一个带明确 HTTP 状态码的响应,而不是让它冒泡成一个
        // 500 页面看不出原因。
        String json;
        try {
            json = objectMapper.writeValueAsString(event);
        } catch (JacksonException e) {
            throw new ResponseStatusException(HttpStatus.INTERNAL_SERVER_ERROR, "消息序列化失败", e);
        }

        // send() 是异步的,返回一个 Future——这里同步等最多 5 秒拿结果,
        // 是为了让"发送失败"这件事能立刻反映到这次 HTTP 响应里,而不是悄悄丢在后台。
        // uploadId 当 key:同一个 upload 的消息固定分到同一个 partition,以后即使
        // 一个 upload 产生多条消息(比如切片进度事件),顺序也有保证。
        try {
            SendResult<String, String> result = kafkaTemplate.send(uploadTopic, uploadId, json)
                    .get(5, TimeUnit.SECONDS);
            log.debug("发送到 partition={} offset={}",
                    result.getRecordMetadata().partition(), result.getRecordMetadata().offset());
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new ResponseStatusException(HttpStatus.INTERNAL_SERVER_ERROR, "发送被中断", e);
        } catch (ExecutionException | TimeoutException e) {
            throw new ResponseStatusException(HttpStatus.INTERNAL_SERVER_ERROR, "发送到 Kafka 失败", e);
        }
    }

    /**
     * Spring 默认的错误页面会把 ResponseStatusException 里写的具体原因隐藏掉,
     * 只返回笼统的 "Bad Request"(防止把内部异常信息泄露给外部调用方)。
     * 试过 server.error.include-message: always 想找回详细信息,没生效——
     * 与其猜配置项名字对不对,不如自己写一个 @ExceptionHandler,行为完全可控,
     * 调试或前端联调时都能直接看到具体是哪条校验没过。
     */
    @ExceptionHandler(ResponseStatusException.class)
    public ResponseEntity<Map<String, String>> handleBadRequest(ResponseStatusException e) {
        return ResponseEntity.status(e.getStatusCode())
                .body(Map.of("error", String.valueOf(e.getReason())));
    }
}
