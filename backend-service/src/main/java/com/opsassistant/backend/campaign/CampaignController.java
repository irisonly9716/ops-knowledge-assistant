package com.opsassistant.backend.campaign;

import com.opsassistant.backend.user.CurrentUser;
import com.opsassistant.backend.user.User;
import java.time.LocalDate;
import java.time.YearMonth;
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
 * Finds a campaign by month. A plan or review is bound to a campaign by its id, and uploaders know "the
 * sale in November", not an id; a name alone isn't enough either, since names repeat ("Beauty Weekly Live
 * Special" runs many times). The flags show whether a plan or review already exists, which decides between
 * mode=new and mode=replace.
 *
 * Staff and managers see their own department. Executive and admin belong to no department and see all.
 */
@RestController
@RequestMapping("/campaigns")
@RequiredArgsConstructor
public class CampaignController {

    private final CampaignRepository campaignRepository;

    public record CampaignResponse(Long campaignId, String name, LocalDate startDate, LocalDate endDate, String status,
                                   boolean hasPlanDoc, boolean hasReviewDoc) {
    }

    @GetMapping
    public List<CampaignResponse> byMonth(@CurrentUser User user, @RequestParam("month") String month) {
        YearMonth period;
        try {
            period = YearMonth.parse(month);
        } catch (DateTimeParseException e) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "month must look like 2025-11, got: " + month);
        }
        List<Campaign> campaigns = user.getDepartmentId() == null
                ? campaignRepository.findRunningBetween(period.atDay(1), period.atEndOfMonth())
                : campaignRepository.findRunningBetween(period.atDay(1), period.atEndOfMonth(), user.getDepartmentId());
        return campaigns.stream()
                .map(c -> new CampaignResponse(c.getCampaignId(), c.getName(), c.getStartDate(), c.getEndDate(),
                        c.getStatus(), c.isHasPlanDoc(), c.isHasReviewDoc()))
                .toList();
    }
}
