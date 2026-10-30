package com.opsassistant.backend.department;

import org.springframework.data.jpa.repository.JpaRepository;

/**
 * The Spring Data JPA pattern: extend JpaRepository<entity type, id type> and save() / findAll() /
 * findById() need no code; the framework generates the implementation at runtime. That is why the
 * simplest Entity + Repository pair is this short.
 */
public interface DepartmentRepository extends JpaRepository<Department, Long> {

    /**
     * The method name is the query. Spring Data JPA splits existsByName at "By", treats the rest as
     * a field name and generates something like
     * "SELECT EXISTS (SELECT 1 FROM departments WHERE name = ?)" without a line of SQL/JPQL.
     * UploadController uses it to check that a department name really exists.
     */
    boolean existsByName(String name);
}
