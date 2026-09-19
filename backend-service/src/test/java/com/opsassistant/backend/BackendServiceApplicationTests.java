package com.opsassistant.backend;

import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.SpringBootTest;

/**
 * 集成测试:启动【完整】的 Spring 容器,连真实的 Postgres。
 *
 * 它真正验证的东西比名字"contextLoads"看起来的多:因为 application.yml 里是
 * ddl-auto: validate,容器启动时 Hibernate 会拿每个 @Entity 去比对真实数据库的表结构——
 * 所以这个测试同时是一个"Entity 跟 sql/02_schema.sql 是否漂移"的检测器。
 * 以后有人改了 schema 忘了改 Entity(或反过来),这里会直接红。
 *
 * 因为依赖外部基础设施(docker compose 里的 Postgres 要在跑),默认不跑——
 * 见 pom.xml 里的 excludedGroups。想跑它:
 *   .\mvnw.cmd test -DexcludedGroups=none -Dgroups=integration
 */
@Tag("integration")
@SpringBootTest
class BackendServiceApplicationTests {

	@Test
	void contextLoads() {
	}

}
