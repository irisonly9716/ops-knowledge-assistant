package com.opsassistant.backend.upload;

import com.fasterxml.jackson.annotation.JsonValue;

/**
 * 上传内容的两种性质,见 docs/architecture.md 的"两条摄入管道"设计:
 * DOCUMENT 走 RAG(切片+embedding),DATA_TABLE 直接解析成结构化行落表。
 */
public enum ContentKind {
    DOCUMENT("document"),
    DATA_TABLE("data_table");

    private final String value;

    ContentKind(String value) {
        this.value = value;
    }

    /**
     * @JsonValue:告诉 Jackson 序列化这个枚举时用 value()(小写、下划线)返回的字符串,
     * 不要用枚举常量名本身(DOCUMENT/DATA_TABLE,大写)——这样发到 Kafka 的 JSON
     * 还是原来 Python 那边认识的 "document"/"data_table",枚举只是 Java 这一侧
     * 内部更安全,不改变跟外界约定好的字符串格式。
     */
    @JsonValue
    public String value() {
        return value;
    }

    /** 把请求里传来的原始字符串转成枚举,匹配不上就抛一个说明白原因的异常。 */
    public static ContentKind fromValue(String raw) {
        if (raw != null) {
            for (ContentKind kind : values()) {
                if (kind.value.equals(raw)) {
                    return kind;
                }
            }
        }
        throw new IllegalArgumentException("contentKind 只能是 document 或 data_table,收到:" + raw);
    }
}
