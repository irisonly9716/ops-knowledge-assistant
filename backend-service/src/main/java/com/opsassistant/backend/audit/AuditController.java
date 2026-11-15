package com.opsassistant.backend.audit;

import com.opsassistant.backend.user.CurrentUser;
import com.opsassistant.backend.user.User;
import java.time.OffsetDateTime;
import java.time.format.DateTimeParseException;
import java.util.List;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

/**
 * The audit of questions, for admin only: system data, which the executive doesn't see either
 * (docs/permission-model.md). Newest first, a page at a time: pass the last row's createdAt as before for the next.
 */
@RestController
@RequestMapping("/audit")
@RequiredArgsConstructor
public class AuditController {

    static final int MAX_LIMIT = 200;

    private final QueryAuditRepository audits;

    @GetMapping("/queries")
    public List<AuditEntry> queries(@CurrentUser User user,
                                    @RequestParam(required = false) Long userId,
                                    @RequestParam(required = false) Boolean crossDepartment,
                                    @RequestParam(required = false) String before,
                                    @RequestParam(defaultValue = "50") int limit) {
        if (!"admin".equals(user.getRole())) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "only admin reads the audit");
        }
        if (limit < 1 || limit > MAX_LIMIT) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "limit must be between 1 and " + MAX_LIMIT);
        }
        OffsetDateTime olderThan;
        try {
            olderThan = before == null ? null : OffsetDateTime.parse(before);
        } catch (DateTimeParseException e) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "before must be a time like 2026-11-14T09:30:00Z");
        }
        return audits.list(new QueryAuditRepository.Filter(userId, crossDepartment, olderThan, limit));
    }
}
