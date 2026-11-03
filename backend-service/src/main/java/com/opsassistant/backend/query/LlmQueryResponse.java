package com.opsassistant.backend.query;

import java.util.List;

/**
 * An answer, or a refusal with no citations. searchedDepartments is null when every department was searched.
 * Examples: contracts/query.examples.json, checked by both test suites.
 */
public record LlmQueryResponse(String status, String answer, List<Citation> citations, List<String> searchedDepartments) {

    /** number is the [n] in the answer text. */
    public record Citation(int number, long docId, String docType, String title, String department,
                           List<String> headingPath, double score) {
    }
}
