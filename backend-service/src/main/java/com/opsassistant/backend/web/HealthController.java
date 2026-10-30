package com.opsassistant.backend.web;

import java.util.Map;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * GET /health, in the same style as llm-service's /health (hand-written, returns a small JSON), so
 * the two services are easy to compare. Actuator's /actuator/health stays too: that is the
 * standard endpoint for infrastructure (Kubernetes probes, monitoring), not for people; the two
 * don't conflict.
 */
@RestController
public class HealthController {

    @GetMapping("/health")
    public Map<String, String> health() {
        return Map.of("status", "ok", "service", "backend-service");
    }
}
