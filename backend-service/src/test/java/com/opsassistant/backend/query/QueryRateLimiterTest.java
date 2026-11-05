package com.opsassistant.backend.query;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import org.junit.jupiter.api.Test;

/** The token bucket with a hand-driven clock: no waiting. */
class QueryRateLimiterTest {

    private static final long SECOND = 1_000_000_000L;

    private long now = 5 * SECOND;

    private QueryRateLimiter limiter(double qps) {
        return new QueryRateLimiter(qps, () -> now);
    }

    private int acquired(QueryRateLimiter limiter, int attempts) {
        int acquired = 0;
        for (int i = 0; i < attempts; i++) {
            if (limiter.acquireOrSecondsToWait() == 0) {
                acquired++;
            }
        }
        return acquired;
    }

    @Test
    void a_whole_second_of_queries_can_arrive_at_once_because_the_peak_is_a_burst() {
        assertThat(acquired(limiter(300), 300)).isEqualTo(300);
    }

    @Test
    void the_next_one_in_the_same_instant_is_refused() {
        QueryRateLimiter limiter = limiter(300);
        acquired(limiter, 300);

        assertThat(limiter.acquireOrSecondsToWait()).isGreaterThan(0);
    }

    @Test
    void the_limit_is_what_gets_through_each_second_under_a_sustained_overload() {
        QueryRateLimiter limiter = limiter(300);
        acquired(limiter, 300);

        for (int second = 0; second < 5; second++) {
            now += SECOND;
            assertThat(acquired(limiter, 1000)).isEqualTo(300);
        }
    }

    @Test
    void tokens_drip_in_proportionally_to_the_time_passed() {
        QueryRateLimiter limiter = limiter(300);
        acquired(limiter, 300);

        now += SECOND / 10;

        assertThat(acquired(limiter, 1000)).isEqualTo(30);
    }

    @Test
    void a_long_quiet_time_saves_up_only_one_second_of_queries() {
        QueryRateLimiter limiter = limiter(300);
        acquired(limiter, 300);

        now += 3600 * SECOND;

        assertThat(acquired(limiter, 1000)).isEqualTo(300);
    }

    @Test
    void refused_attempts_cost_nothing() {
        QueryRateLimiter limiter = limiter(300);
        acquired(limiter, 300);
        acquired(limiter, 10_000);

        now += SECOND;

        assertThat(acquired(limiter, 1000)).isEqualTo(300);
    }

    @Test
    void the_wait_is_at_least_one_whole_second_at_a_high_rate() {
        QueryRateLimiter limiter = limiter(300);
        acquired(limiter, 300);

        assertThat(limiter.acquireOrSecondsToWait()).isEqualTo(1);
    }

    @Test
    void the_wait_is_how_long_the_next_token_takes_at_a_low_rate() {
        QueryRateLimiter limiter = limiter(0.1);   // one every 10 s, and the bucket holds a tenth of one

        assertThat(limiter.acquireOrSecondsToWait()).isEqualTo(9);
    }

    @Test
    void a_limit_can_be_raised_for_the_next_target() {
        assertThat(acquired(limiter(500), 600)).isEqualTo(500);
    }

    @Test
    void many_threads_at_once_get_exactly_the_tokens_there_are() throws Exception {
        QueryRateLimiter limiter = limiter(300);
        Callable<Boolean> attempt = () -> limiter.acquireOrSecondsToWait() == 0;
        List<Future<Boolean>> results = new ArrayList<>();
        try (ExecutorService pool = Executors.newVirtualThreadPerTaskExecutor()) {
            for (int i = 0; i < 1000; i++) {
                results.add(pool.submit(attempt));
            }
        }
        int acquired = 0;
        for (Future<Boolean> result : results) {
            acquired += result.get() ? 1 : 0;
        }
        assertThat(acquired).isEqualTo(300);
    }

    @Test
    void a_limit_of_zero_is_refused_at_startup() {
        assertThatThrownBy(() -> limiter(0)).isInstanceOf(IllegalArgumentException.class);
    }
}
