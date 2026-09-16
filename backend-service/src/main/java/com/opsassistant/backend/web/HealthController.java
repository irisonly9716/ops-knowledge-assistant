package com.opsassistant.backend.web;

import java.util.Map;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 大纲要的 GET /health。跟 llm-service 的 /health 风格保持一致(都是纯手写、返回一个
 * 简单 JSON),方便两个服务对着看。Actuator 自带的 /actuator/health 也保留着——
 * 那个是给基础设施(K8s 探活、监控)用的标准接口,不是给人手动看的,两者不冲突。
 */
@RestController
public class HealthController {

    @GetMapping("/health")
    public Map<String, String> health() {
        return Map.of("status", "ok", "service", "backend-service");
    }
}
