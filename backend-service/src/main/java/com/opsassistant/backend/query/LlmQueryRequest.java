package com.opsassistant.backend.query;

/**
 * What llm-service receives. sessionId: the conversation the question belongs to, null for a question on its own;
 * llm-service keeps a conversation under the user id as well, so one user can't read another's.
 * Examples: contracts/query.examples.json, checked by both test suites.
 */
public record LlmQueryRequest(String question, Long userId, String role, String department, String sessionId) {
}
