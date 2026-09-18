package com.opsassistant.backend.upload;

/**
 * 发到 Kafka document-upload-events 的消息体。
 *
 * contentKind/docType/tableType/source 都是枚举,不是裸字符串——防止 Java 这一侧
 * 手滑拼错值。序列化成 JSON 时(见各枚举的 @JsonValue),字段还是写成外界约定好的
 * 小写字符串("document"/"sop"...),跟 llm-service(Python)和数据库的 CHECK 约束
 * 保持一致;枚举只让 Java 内部更安全,不改变消息在"线上"的实际格式。
 *
 * record 是 Java 的语法糖:声明一次字段,自动生成构造器 + getter(方法名跟字段名
 * 一样,不带 get 前缀,比如 uploadId() 不是 getUploadId())+ equals/hashCode/toString,
 * 而且所有字段都是 final 的(创建后不能改)。
 */
public record UploadEvent(
        String uploadId,
        String path,
        ContentKind contentKind,
        DocType docType,       // 仅 contentKind=DOCUMENT 时非空
        TableType tableType,   // 仅 contentKind=DATA_TABLE 时非空
        String department,
        Long campaignId,       // 可空:sop 类型不挂 campaign
        String category,       // 可空
        Source source
) {
}
