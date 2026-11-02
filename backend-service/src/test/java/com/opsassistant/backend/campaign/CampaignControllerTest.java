package com.opsassistant.backend.campaign;

import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.opsassistant.backend.user.User;
import com.opsassistant.backend.user.UserRepository;
import java.time.LocalDate;
import java.util.List;
import java.util.Optional;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

/** GET /campaigns?month=: who sees what, and which days a month means. The query itself: UploadFlowIntegrationTest. */
@WebMvcTest(CampaignController.class)
class CampaignControllerTest {

    @Autowired
    MockMvc mockMvc;
    @MockitoBean
    CampaignRepository campaignRepository;
    @MockitoBean
    UserRepository userRepository;

    private final Campaign campaign = new Campaign(11L, "Beauty Weekly Live Special", 1L, LocalDate.of(2026, 9, 28),
            LocalDate.of(2026, 11, 3), "running", true, false);

    @BeforeEach
    void users() {
        when(userRepository.findById(5L)).thenReturn(Optional.of(new User(5L, "Staff", 1L, "staff")));
        when(userRepository.findById(8L)).thenReturn(Optional.of(new User(8L, "Executive", null, "executive")));
    }

    @Test
    void staff_see_the_campaigns_of_their_own_department_with_what_already_exists() throws Exception {
        when(campaignRepository.findRunningBetween(LocalDate.of(2026, 10, 1), LocalDate.of(2026, 10, 31), 1L))
                .thenReturn(List.of(campaign));

        mockMvc.perform(get("/campaigns").param("month", "2026-10").header("X-User-Id", "5"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.length()").value(1))
                .andExpect(jsonPath("$[0].campaignId").value(11))
                .andExpect(jsonPath("$[0].name").value("Beauty Weekly Live Special"))
                .andExpect(jsonPath("$[0].startDate").value("2026-09-28"))
                .andExpect(jsonPath("$[0].status").value("running"))
                .andExpect(jsonPath("$[0].hasPlanDoc").value(true))
                .andExpect(jsonPath("$[0].hasReviewDoc").value(false));
    }

    @Test
    void someone_with_no_department_sees_all_departments() throws Exception {
        when(campaignRepository.findRunningBetween(LocalDate.of(2026, 10, 1), LocalDate.of(2026, 10, 31)))
                .thenReturn(List.of(campaign));

        mockMvc.perform(get("/campaigns").param("month", "2026-10").header("X-User-Id", "8"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.length()").value(1));
    }

    @Test
    void a_month_ends_on_its_real_last_day() throws Exception {
        mockMvc.perform(get("/campaigns").param("month", "2024-02").header("X-User-Id", "5")).andExpect(status().isOk());

        verify(campaignRepository).findRunningBetween(LocalDate.of(2024, 2, 1), LocalDate.of(2024, 2, 29), 1L);
    }

    @ParameterizedTest
    @CsvSource({"2026-13", "10-2026", "october", "2026-10-01", "''"})
    void a_month_that_is_not_a_month_is_a_bad_request(String month) throws Exception {
        mockMvc.perform(get("/campaigns").param("month", month).header("X-User-Id", "5")).andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").exists());
    }

    @Test
    void the_month_is_required() throws Exception {
        mockMvc.perform(get("/campaigns").header("X-User-Id", "5")).andExpect(status().isBadRequest());
    }

    @Test
    void campaigns_need_a_known_user() throws Exception {
        mockMvc.perform(get("/campaigns").param("month", "2026-10")).andExpect(status().isUnauthorized());
        mockMvc.perform(get("/campaigns").param("month", "2026-10").header("X-User-Id", "99")).andExpect(status().isUnauthorized());
    }
}
