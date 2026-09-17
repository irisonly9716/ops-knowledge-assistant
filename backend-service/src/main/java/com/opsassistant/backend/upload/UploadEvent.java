package com.opsassistant.backend.upload;

/**
 * 发到 Kafka document-upload-events 的消息体。
 *
 * record 是 Java 的一个语法糖:声明一次字段,自动生成构造器 + getter(方法名跟字段名
 * 一样,不带 get 前缀,比如 uploadId() 不是 getUploadId())+ equals/hashCode/toString,
 * 而且所有字段都是 final 的(创建后不能改)。这里没用 Lombok 是因为 record 本身就是
 * "不可变数据载体"的标准写法,比 Lombok 的注解组合更简洁,Spring/Jackson 都原生支持。
 *
 * docType/tableType/campaignId/category 允许为 null——具体哪些字段该填,
 * 取决于 contentKind 是 "document" 还是 "data_table",校验逻辑在 UploadController 里。
 */
public record UploadEvent(
        String uploadId,
        String path,
        String contentKind,   // "document" | "data_table"
        String docType,       // sop | campaign_plan | campaign_review(仅 document 用)
        String tableType,     // campaign_products | campaign_customer_behavior(仅 data_table 用)
        String department,
        Long campaignId,      // 可空:sop 类型不挂 campaign
        String category,      // 可空
        String source         // "manual" | "auto"
) {
}
