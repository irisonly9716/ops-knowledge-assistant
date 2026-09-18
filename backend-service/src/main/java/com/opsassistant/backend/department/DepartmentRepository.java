package com.opsassistant.backend.department;

import org.springframework.data.jpa.repository.JpaRepository;

/**
 * Spring Data JPA 的套路:只要继承 JpaRepository<实体类型, 主键类型>,
 * save() / findAll() / findById() 这些方法都不用自己写,框架在运行时生成实现。
 * 这也是为什么大纲说"最简单的 Entity + Repository"——正常情况下就是这么短。
 */
public interface DepartmentRepository extends JpaRepository<Department, Long> {

    /**
     * 方法名本身就是查询——Spring Data JPA 看到 existsByName 这个方法签名,
     * 按"按 By 拆开、后面跟的是字段名"这个约定,自动生成
     * "SELECT EXISTS (SELECT 1 FROM departments WHERE name = ?)" 这样的查询,
     * 不用写一行 SQL/JPQL。用来在 UploadController 里校验"这个部门名真的存在吗"。
     */
    boolean existsByName(String name);
}
