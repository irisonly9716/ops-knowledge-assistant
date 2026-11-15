package com.opsassistant.backend.audit;

import com.opsassistant.backend.user.CurrentUser;
import com.opsassistant.backend.user.User;
import java.util.Set;
import java.util.UUID;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

/**
 * The asker's thumbs up or down on an answer, named by the queryId it came back with. Only the asker rates their own
 * answer: the queryId alone would let anyone who saw it rate it. A second rating replaces the first.
 */
@RestController
@RequiredArgsConstructor
public class FeedbackController {

    /** The same limit as the CHECK on query_feedback.comment, counted as Postgres counts characters. */
    static final int MAX_COMMENT_CHARS = 1000;
    static final Set<String> RATINGS = Set.of("up", "down");

    /** comment: optional; blank counts as none. */
    public record FeedbackRequest(String rating, String comment) {
    }

    private final QueryAuditRepository audits;

    @PutMapping("/query/{queryId}/feedback")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void rate(@CurrentUser User user, @PathVariable UUID queryId, @RequestBody FeedbackRequest body) {
        String rating = body.rating() == null ? "" : body.rating().strip();
        if (!RATINGS.contains(rating)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "rating must be up or down");
        }
        String comment = body.comment() == null || body.comment().isBlank() ? null : body.comment().strip();
        if (comment != null && comment.codePointCount(0, comment.length()) > MAX_COMMENT_CHARS) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "comment is longer than " + MAX_COMMENT_CHARS + " characters");
        }
        long asker = audits.askerOf(queryId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "unknown query: " + queryId));
        if (asker != user.getUserId()) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "only the asker can rate an answer");
        }
        audits.saveFeedback(queryId, rating, comment);
    }
}
