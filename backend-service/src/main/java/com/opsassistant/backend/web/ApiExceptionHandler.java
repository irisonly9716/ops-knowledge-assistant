package com.opsassistant.backend.web;

import java.util.Map;
import lombok.extern.slf4j.Slf4j;
import org.springframework.core.NestedRuntimeException;
import org.springframework.dao.DataAccessResourceFailureException;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.transaction.CannotCreateTransactionException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.multipart.MaxUploadSizeExceededException;
import org.springframework.web.server.ResponseStatusException;

/**
 * Spring's default error response hides the reason inside a ResponseStatusException and returns
 * a generic "Bad Request" (so internal exception details don't leak to callers).
 * server.error.include-message: always was tried to get the details back and had no effect;
 * instead of guessing configuration keys, this handler makes the behavior fully explicit for every
 * controller, and debugging or frontend integration shows exactly which rule failed.
 */
@Slf4j
@RestControllerAdvice
public class ApiExceptionHandler {

    @ExceptionHandler(ResponseStatusException.class)
    public ResponseEntity<Map<String, String>> handle(ResponseStatusException e) {
        return ResponseEntity.status(e.getStatusCode())
                .headers(e.getHeaders())   // Retry-After, when the exception has one
                .body(Map.of("error", String.valueOf(e.getReason())));
    }

    /** The limits in application.yml ran out, with Postgres down, frozen or out of connections. */
    @ExceptionHandler({CannotCreateTransactionException.class, DataAccessResourceFailureException.class})
    public ResponseEntity<Map<String, String>> noDatabase(NestedRuntimeException e) {
        log.warn("no database connection: {}", e.getMostSpecificCause().getMessage());
        return ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE)
                .body(Map.of("error", "the database is unavailable; try again shortly"));
    }

    /** The default answer here has another shape, and it can quote the input back. */
    @ExceptionHandler(HttpMessageNotReadableException.class)
    public ResponseEntity<Map<String, String>> unreadable(HttpMessageNotReadableException e) {
        return ResponseEntity.badRequest().body(Map.of("error", "the request body is not valid JSON"));
    }

    /** Over the limit in application.yml. Thrown while the request is parsed, before any controller runs. */
    @ExceptionHandler(MaxUploadSizeExceededException.class)
    public ResponseEntity<Map<String, String>> tooLarge(MaxUploadSizeExceededException e) {
        return ResponseEntity.status(HttpStatus.CONTENT_TOO_LARGE)
                .body(Map.of("error", "the file is too large; the limit is 20 MB"));
    }
}
