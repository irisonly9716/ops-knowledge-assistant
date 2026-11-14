package com.opsassistant.backend.query;

import java.util.List;

/**
 * An answer, a refusal with no citations, "failed" when no query that answers the question could be written, or
 * "clarify" when it is asked back. searchedDepartments is null when every department was searched. paths are the
 * sources that ran, "documents" and "data"; data is the query an answer rests on with its rows, or null when it rests on
 * none. basis is the campaign a question like "the last Black Friday" turned out to mean; candidates, only when asked
 * back, are what it may mean. Examples: contracts/query.examples.json, checked by both test suites.
 */
public record LlmQueryResponse(String status, String answer, List<Citation> citations, List<String> searchedDepartments,
                               List<String> paths, Data data, Campaign basis, List<Campaign> candidates) {

    /** number is the [n] in the answer text. */
    public record Citation(int number, long docId, String docType, String title, String department,
                           List<String> headingPath, double score) {
    }

    /**
     * Every value is text or null, as the database returned it, so amounts stay exact: this service passes them on and
     * never reads them as numbers. truncated: the query returned more rows than are kept.
     */
    public record Data(String sql, List<String> columns, List<List<String>> rows, boolean truncated) {
    }

    /** Dates as ISO text, passed on as they come. */
    public record Campaign(String department, String campaignName, String startDate, String endDate) {
    }
}
