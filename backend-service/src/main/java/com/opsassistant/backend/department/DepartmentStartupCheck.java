package com.opsassistant.backend.department;

import java.util.List;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.CommandLineRunner;
import org.springframework.stereotype.Component;

/**
 * Day 4 的验证代码:应用启动后自己写一条、读回来,打日志证明"连上 Postgres 了、
 * JPA 能读写"。这是一次性的检查,不是产品逻辑——Day 5 写真正的模拟数据生成脚本后,
 * 这个类可以删掉。
 *
 * CommandLineRunner 是 Spring Boot 的一个钩子接口:容器启动完、
 * 所有 Bean(包括下面注入的 repository)都装配好之后,自动调用 run()。
 * 常用来做这种"启动时跑一次"的初始化/自检逻辑。
 */
@Slf4j
@Component
@RequiredArgsConstructor // Lombok:自动生成一个"给 final 字段赋值"的构造器,Spring 用它做依赖注入
public class DepartmentStartupCheck implements CommandLineRunner {

    private final DepartmentRepository departmentRepository;

    @Override
    public void run(String... args) {
        if (departmentRepository.count() == 0) {
            departmentRepository.save(new Department(null, "美妆运营"));
            log.info("✅ 写入成功:插入了一条 department");
        }

        List<Department> all = departmentRepository.findAll();
        log.info("✅ 读取成功,当前 departments 表内容: {}",
                all.stream().map(Department::getName).toList());
    }
}
