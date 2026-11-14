package com.opsassistant.backend.config;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
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
 *
 * llm-service reads the same file with python-dotenv, so a line has to mean the same here, or a value the two services
 * share, such as INTERNAL_SERVICE_TOKEN, differs between them and every query is refused with a 401. So, as there: an
 * "export " in front is dropped, a value in matching quotes is what is inside them and one whose quote never closes is
 * skipped, an unquoted value ends where whitespace and # start a comment, a name alone on its line unsets it, and the
 * whitespace stripped is what Python strips. Java's String.strip() keeps a non-breaking space, which Python removes.
 */
public class DotenvEnvironmentPostProcessor implements EnvironmentPostProcessor {

    /** Whitespace and then #: where a comment starts after an unquoted value, as python-dotenv reads it. */
    private static final Pattern COMMENT = Pattern.compile("\\s#");

    @Override
    public void postProcessEnvironment(ConfigurableEnvironment environment, SpringApplication application) {
        // .env lives at the repository root, the parent of backend-service/
        Path envFile = Path.of("..", ".env");
        if (!Files.exists(envFile)) {
            return; // no .env is fine: production should use real operating-system environment variables
        }

        Map<String, Object> values;
        try {
            values = parse(Files.readAllLines(envFile));
        } catch (IOException e) {
            throw new IllegalStateException("failed to read .env: " + envFile.toAbsolutePath(), e);
        }

        // addLast: lowest priority. A real operating-system environment variable set in a deployment
        // wins over the same name in .env; .env is only the fallback when no real variable exists,
        // the same role it plays for docker compose.
        environment.getPropertySources().addLast(new MapPropertySource("dotenv", values));
    }

    /** The variables of a .env file's lines. A name given twice keeps the last value, as in python-dotenv. */
    static Map<String, Object> parse(List<String> lines) {
        Map<String, Object> values = new LinkedHashMap<>();
        for (String line : lines) {
            String stripped = strip(line);
            if (stripped.startsWith("export ")) {
                stripped = strip(stripped.substring("export ".length()));
            }
            if (stripped.isEmpty() || stripped.startsWith("#")) {
                continue;
            }
            int eq = stripped.indexOf('=');
            if (eq < 0) {
                values.remove(stripped);
                continue;
            }
            String value = value(strip(stripped.substring(eq + 1)));
            if (value != null) {
                values.put(strip(stripped.substring(0, eq)), value);
            }
        }
        return values;
    }

    /** What is inside matching quotes; up to a comment otherwise; null when an opening quote never closes. */
    private static String value(String raw) {
        if (!raw.isEmpty() && (raw.charAt(0) == '"' || raw.charAt(0) == '\'')) {
            int close = raw.indexOf(raw.charAt(0), 1);
            return close > 0 ? raw.substring(1, close) : null;
        }
        Matcher comment = COMMENT.matcher(raw);
        return strip(comment.find() ? raw.substring(0, comment.start()) : raw);
    }

    /** Whitespace off both ends as Python's str.strip() takes it, non-breaking spaces included. */
    static String strip(String text) {
        int start = 0;
        int end = text.length();
        while (start < end && isSpace(text.charAt(start))) {
            start++;
        }
        while (end > start && isSpace(text.charAt(end - 1))) {
            end--;
        }
        return text.substring(start, end);
    }

    private static boolean isSpace(char c) {
        return Character.isWhitespace(c) || Character.isSpaceChar(c) || c == '\u0085';
    }
}
