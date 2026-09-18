package com.opsassistant.backend.upload;

import com.fasterxml.jackson.annotation.JsonValue;

/**
 * 谁发起的上传。现在这个接口只服务"员工手动上传",一直是 MANUAL;
 * AUTO 是留给 docs/architecture.md 里提到的"未来定时任务自动摄取"用的,
 * 现在还没有对应的代码调用它,先把枚举值定好。
 */
public enum Source {
    MANUAL("manual"),
    AUTO("auto");

    private final String value;

    Source(String value) {
        this.value = value;
    }

    @JsonValue
    public String value() {
        return value;
    }
}
