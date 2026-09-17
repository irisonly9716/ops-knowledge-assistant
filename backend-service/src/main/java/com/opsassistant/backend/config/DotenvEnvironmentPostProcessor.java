package com.opsassistant.backend.config;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.env.EnvironmentPostProcessor;
import org.springframework.core.env.ConfigurableEnvironment;
import org.springframework.core.env.MapPropertySource;

/**
 * Java 默认不认识仓库根目录那个 .env 文件——这个类是我们自己补的"读 .env 的能力",
 * 让 application.yml 里 ${POSTGRES_USER:ops} 这种占位符真的有机会从 .env 拿到值,
 * 而不是永远退到冒号后面写死的默认值(这是 Day 5 发现的一个真实 bug 的修复)。
 *
 * EnvironmentPostProcessor 是 Spring Boot 提供的一个扩展点:启动过程中,
 * 比读 application.yml 还早的阶段,会调用所有注册了的 EnvironmentPostProcessor。
 * 我们在这里手动解析 .env,塞进 Spring 的 Environment 里。
 *
 * 这个类不像 @Component / @RestController 那样靠"扫描注解"被发现——
 * EnvironmentPostProcessor 跑的时候,Spring 的容器(能扫描注解那套机制)还没建好,
 * 所以必须用更原始的方式注册,见 resources/META-INF/spring.factories。
 */
public class DotenvEnvironmentPostProcessor implements EnvironmentPostProcessor {

    @Override
    public void postProcessEnvironment(ConfigurableEnvironment environment, SpringApplication application) {
        // backend-service/ 的上一级就是仓库根目录,.env 放在那里
        Path envFile = Path.of("..", ".env");
        if (!Files.exists(envFile)) {
            return; // 没有 .env 不报错——生产环境该用真正的操作系统环境变量,不依赖这个文件
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
            throw new IllegalStateException("读取 .env 失败: " + envFile.toAbsolutePath(), e);
        }

        // addLast:优先级设成最低。如果真的在部署环境里设了货真价实的操作系统环境变量,
        // 那个优先级更高会覆盖 .env 里的同名值——.env 只在没有真环境变量时兜底,
        // 跟 docker-compose 自己读 .env 的角色定位一致。
        environment.getPropertySources().addLast(new MapPropertySource("dotenv", values));
    }
}
