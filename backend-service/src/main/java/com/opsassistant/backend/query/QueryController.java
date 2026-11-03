package com.opsassistant.backend.query;

import com.opsassistant.backend.department.Department;
import com.opsassistant.backend.department.DepartmentRepository;
import com.opsassistant.backend.user.CurrentUser;
import com.opsassistant.backend.user.User;
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

    /** What a client sends. Unknown fields, such as a department of its own choosing, are ignored. */
    public record QueryRequest(String question) {
    }

    private final QueryClient queryClient;
    private final DepartmentRepository departmentRepository;

    @PostMapping("/query")
    public LlmQueryResponse query(@CurrentUser User user, @RequestBody QueryRequest body) {
        String question = body.question() == null ? "" : body.question().strip();
        if (question.isEmpty()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "question is required");
        }
        if (question.length() > MAX_QUESTION_CHARS) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "question is longer than " + MAX_QUESTION_CHARS + " characters");
        }
        return queryClient.ask(new LlmQueryRequest(question, user.getUserId(), user.getRole(), departmentOf(user)));
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
