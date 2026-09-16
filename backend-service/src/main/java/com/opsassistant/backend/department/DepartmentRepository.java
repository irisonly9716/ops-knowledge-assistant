package com.opsassistant.backend.department;

import org.springframework.data.jpa.repository.JpaRepository;

/**
 * Spring Data JPA 的套路:只要继承 JpaRepository<实体类型, 主键类型>,
 * save() / findAll() / findById() 这些方法都不用自己写,框架在运行时生成实现。
 * 这也是为什么大纲说"最简单的 Entity + Repository"——正常情况下就是这么短。
 */
public interface DepartmentRepository extends JpaRepository<Department, Long> {
}
