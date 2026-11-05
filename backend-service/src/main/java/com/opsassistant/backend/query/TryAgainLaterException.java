package com.opsassistant.backend.query;

import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatusCode;
import org.springframework.web.server.ResponseStatusException;

/** A refusal that says when to come back, in the Retry-After header. */
public class TryAgainLaterException extends ResponseStatusException {

    private final String retryAfter;

    public TryAgainLaterException(HttpStatusCode status, String reason, String retryAfter) {
        super(status, reason);
        this.retryAfter = retryAfter;
    }

    @Override
    public HttpHeaders getHeaders() {
        HttpHeaders headers = new HttpHeaders();
        headers.set(HttpHeaders.RETRY_AFTER, retryAfter);
        return headers;
    }
}
