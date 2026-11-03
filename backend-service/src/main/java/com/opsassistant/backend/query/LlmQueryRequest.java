package com.opsassistant.backend.query;

/** What llm-service receives. Examples: contracts/query.examples.json, checked by both test suites. */
public record LlmQueryRequest(String question, Long userId, String role, String department) {
}
