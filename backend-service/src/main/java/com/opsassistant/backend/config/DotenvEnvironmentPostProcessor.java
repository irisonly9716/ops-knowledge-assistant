package com.opsassistant.backend.config;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.boot.EnvironmentPostProcessor;
import org.springframework.boot.SpringApplication;
import org.springframework.core.env.ConfigurableEnvironment;
import org.springframework.core.env.MapPropertySource;

/**
 * Java does not read the .env file at the repository root by itself; this class adds that ability,
 * so placeholders like ${POSTGRES_USER:ops} in application.yml really get their value from .env
 * instead of always falling back to the default after the colon.
 *
 * EnvironmentPostProcessor is a Spring Boot extension point: during startup, even before
 * application.yml is read, every registered EnvironmentPostProcessor is called. This one parses
 * .env by hand and adds it to Spring's Environment.
 *
 * Unlike @Component / @RestController, it is not discovered by annotation scanning: when an
 * EnvironmentPostProcessor runs, Spring's container (the machinery that scans annotations) does not
 * exist yet, so it has to be registered the more primitive way, in resources/META-INF/spring.factories.
 *
 * Mind the interface's package: Spring Boot 4 moved it from org.springframework.boot.env to
 * org.springframework.boot and marked the old one @Deprecated(forRemoval = true). The import and the
 * registration key in spring.factories must BOTH use the new name: changing only one makes startup
 * throw "not assignable to factory type" (verified), a loud failure. What fails SILENTLY is not
 * registering it at all (spring.factories missing or the key misspelled): .env is then never read
 * and application.yml falls back to the defaults after the colon.
 */
public class DotenvEnvironmentPostProcessor implements EnvironmentPostProcessor {

    @Override
    public void postProcessEnvironment(ConfigurableEnvironment environment, SpringApplication application) {
        // .env lives at the repository root, the parent of backend-service/
        Path envFile = Path.of("..", ".env");
        if (!Files.exists(envFile)) {
            return; // no .env is fine: production should use real operating-system environment variables
        }

        Map<String, Object> values = new LinkedHashMap<>();
        try {
            for (String line : Files.readAllLines(envFile)) {
                String trimmed = line.trim();
                if (trimmed.isEmpty() || trimmed.startsWith("#") || !trimmed.contains("=")) {
                    continue;
                }
                int eq = trimmed.indexOf('=');
                values.put(trimmed.substring(0, eq).trim(), trimmed.substring(eq + 1).trim());
            }
        } catch (IOException e) {
            throw new IllegalStateException("failed to read .env: " + envFile.toAbsolutePath(), e);
        }

        // addLast: lowest priority. A real operating-system environment variable set in a deployment
        // wins over the same name in .env; .env is only the fallback when no real variable exists,
        // the same role it plays for docker compose.
        environment.getPropertySources().addLast(new MapPropertySource("dotenv", values));
    }
}
