package com.opsassistant.backend.campaign;

import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import lombok.AllArgsConstructor;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

/**
 * 对应 db/migration/ 里建的 campaigns 表。这个项目里 campaign 不是通过
 * backend-service 创建的(是造假数据脚本造的,现实场景可能是另一个系统同步过来),
 * 所以没有 @GeneratedValue——这个实体现在只用来"查一个 campaignId 是否存在",
 * 不负责生成新的。字段也只映射了用得到的几个,不是 campaigns 表全部的列
 * (Hibernate 的 validate 模式只检查"映射了的字段跟表对不对得上",不要求
 * 映射表里的每一列)。
 */
@Entity
@Table(name = "campaigns")
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
public class Campaign {

    @Id
    private Long campaignId;

    private String name;
}
