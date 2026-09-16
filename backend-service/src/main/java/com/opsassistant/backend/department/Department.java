package com.opsassistant.backend.department;

import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import lombok.AllArgsConstructor;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

/**
 * 对应 docs/data-schema.md 里的 departments 表——权限模型的基础表之一
 * (每个 campaign / anchor / user 都会挂在某个 department 下)。
 *
 * Lombok 的 @Getter/@Setter/构造器注解:省掉手写一堆 getXxx()/setXxx(),
 * pom.xml 里的 lombok 依赖 + IDE 装 Lombok 插件后,这些方法在编译期自动生成。
 */
@Entity
@Table(name = "departments")
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
public class Department {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long departmentId;

    private String name;
}
