package com.opsassistant.backend.upload;

import com.fasterxml.jackson.annotation.JsonValue;

/**
 * 对应 db/migration/ 里 documents.doc_type 和 uploads.doc_type 的 CHECK 约束
 * (IN ('sop','campaign_plan','campaign_review'))——这三个值必须跟那条约束
 * 完全一致,改数据库的约束时记得回来改这里,反过来也一样,枚举不会替你自动同步。
 */
public enum DocType {
    SOP("sop"),
    CAMPAIGN_PLAN("campaign_plan"),
    CAMPAIGN_REVIEW("campaign_review");

    private final String value;

    DocType(String value) {
        this.value = value;
    }

    @JsonValue
    public String value() {
        return value;
    }

    /**
     * 是不是必须挂一个 campaign——把这条业务规则放在枚举自己身上(而不是在
     * UploadController 里写 !"sop".equals(docType)),这样以后新增文档类型时,
     * 这条规则跟着新增的枚举常量一起写,不会漏掉。
     */
    public boolean needsCampaign() {
        return this != SOP;
    }

    public static DocType fromValue(String raw) {
        if (raw != null) {
            for (DocType type : values()) {
                if (type.value.equals(raw)) {
                    return type;
                }
            }
        }
        throw new IllegalArgumentException(
                "docType 只能是 sop / campaign_plan / campaign_review,收到:" + raw);
    }
}
