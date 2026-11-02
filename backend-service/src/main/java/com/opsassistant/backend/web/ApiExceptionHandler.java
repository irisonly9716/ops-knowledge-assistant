package com.opsassistant.backend.web;

import java.util.Map;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
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
@RestControllerAdvice
public class ApiExceptionHandler {

    @ExceptionHandler(ResponseStatusException.class)
    public ResponseEntity<Map<String, String>> handle(ResponseStatusException e) {
        return ResponseEntity.status(e.getStatusCode())
                .body(Map.of("error", String.valueOf(e.getReason())));
    }

    /** Over the limit in application.yml. Thrown while the request is parsed, before any controller runs. */
    @ExceptionHandler(MaxUploadSizeExceededException.class)
    public ResponseEntity<Map<String, String>> tooLarge(MaxUploadSizeExceededException e) {
        return ResponseEntity.status(HttpStatus.CONTENT_TOO_LARGE)
                .body(Map.of("error", "the file is too large; the limit is 20 MB"));
    }
}
