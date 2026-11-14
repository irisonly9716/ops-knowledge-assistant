package com.opsassistant.backend.query;

import com.opsassistant.backend.department.Department;
import com.opsassistant.backend.department.DepartmentRepository;
import com.opsassistant.backend.user.CurrentUser;
import com.opsassistant.backend.user.User;
import java.util.UUID;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

/**
 * The way in for questions. The client sends only the question: who is asking comes from @CurrentUser, and the
 * role and department from the users table, so nobody can read another department by claiming to belong to it.
 * llm-service trusts what this class sends, so this is the one place that decides it.
 */
@RestController
@RequiredArgsConstructor
public class QueryController {

    /** The same limit as llm-service: a longer question is refused here, not after a trip over the network. */
    static final int MAX_QUESTION_CHARS = 2000;

    /**
     * What a client sends. Unknown fields, such as a department of its own choosing, are ignored. sessionId: a UUID the
     * client makes for each conversation, so a follow-up is understood with the turns before it; none for a question on
     * its own.
     */
    public record QueryRequest(String question, String sessionId) {
    }

    private final QueryClient queryClient;
    private final DepartmentRepository departmentRepository;
    private final QueryRateLimiter rateLimiter;

    @PostMapping("/query")
    public LlmQueryResponse query(@CurrentUser User user, @RequestBody QueryRequest body) {
        String question = body.question() == null ? "" : body.question().strip();
        if (question.isEmpty()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "question is required");
        }
        if (question.length() > MAX_QUESTION_CHARS) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "question is longer than " + MAX_QUESTION_CHARS + " characters");
        }
        String sessionId = sessionIdOf(body.sessionId());
        // After the checks above, so an unknown user or a malformed request can't use up the limit of real questions
        long secondsToWait = rateLimiter.acquireOrSecondsToWait();
        if (secondsToWait > 0) {
            throw new TryAgainLaterException(HttpStatus.TOO_MANY_REQUESTS, "too many questions right now; try again shortly",
                    String.valueOf(secondsToWait));
        }
        return queryClient.ask(new LlmQueryRequest(question, user.getUserId(), user.getRole(), departmentOf(user), sessionId));
    }

    /** A UUID in its usual form, or null; anything else is a 400, checked here rather than after a trip to llm-service. */
    private static String sessionIdOf(String sessionId) {
        if (sessionId == null || sessionId.isBlank()) {
            return null;
        }
        try {
            return UUID.fromString(sessionId.strip()).toString();
        } catch (IllegalArgumentException e) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "sessionId is not a UUID");
        }
    }

    /** The department's name, as documents store it; null for executive and admin, who belong to none. */
    private String departmentOf(User user) {
        if (user.getDepartmentId() == null) {
            return null;
        }
        return departmentRepository.findById(user.getDepartmentId())
                .map(Department::getName)
                .orElseThrow(() -> new IllegalStateException("user " + user.getUserId() + " has no valid department"));
    }
}
