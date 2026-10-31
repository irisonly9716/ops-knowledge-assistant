package com.opsassistant.backend;

import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.SpringBootTest;

/**
 * Integration test: starts the FULL Spring container against a real Postgres.
 *
 * It checks more than the name "contextLoads" suggests: application.yml has ddl-auto: validate,
 * so at startup Hibernate compares every @Entity with the real database tables. That makes this
 * test a detector for drift between the entities and the migrations in db/migration/: if someone
 * changes the schema and forgets an entity (or the other way round), it fails.
 *
 * It needs external infrastructure (the Postgres from docker compose must be running), so it is
 * skipped by default; see excludedGroups in pom.xml. To run it:
 *   .\mvnw.cmd test -DexcludedGroups=none -Dgroups=integration
 */
@Tag("integration")
@SpringBootTest(properties = "outbox.relay.enabled=false")   // no scheduled relay sending real outbox rows during a test
class BackendServiceApplicationTests {

	@Test
	void contextLoads() {
	}

}
