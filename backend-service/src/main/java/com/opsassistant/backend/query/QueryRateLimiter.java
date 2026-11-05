package com.opsassistant.backend.query;

import java.util.function.LongSupplier;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

/**
 * A token bucket: tokens drip in at the limit per second and a query takes one. It holds at most one second's worth,
 * because the peak is a burst, half of the staff asking in the same second: all of them get through, while a sustained
 * rate above the limit runs out and gets refused at once instead of joining a queue.
 *
 * In memory, so the limit applies per backend instance: with several, divide it among them. Redis would share it, at
 * the price of a network call and one more thing that can fail on every query.
 */
@Component
public class QueryRateLimiter {

    private static final double NANOS_PER_SECOND = 1_000_000_000.0;

    private final double ratePerSecond;
    private final LongSupplier nanoClock;
    private double tokens;
    private long lastRefill;

    @Autowired
    public QueryRateLimiter(@Value("${query.rate-limit-qps}") double ratePerSecond) {
        this(ratePerSecond, System::nanoTime);
    }

    QueryRateLimiter(double ratePerSecond, LongSupplier nanoClock) {
        if (ratePerSecond <= 0) {
            throw new IllegalArgumentException("query.rate-limit-qps must be above 0, got: " + ratePerSecond);
        }
        this.ratePerSecond = ratePerSecond;
        this.nanoClock = nanoClock;
        this.tokens = ratePerSecond;
        this.lastRefill = nanoClock.getAsLong();
    }

    /** Takes a token and returns 0, or takes nothing and returns how many whole seconds to wait. */
    public synchronized long acquireOrSecondsToWait() {
        long now = nanoClock.getAsLong();
        tokens = Math.min(ratePerSecond, tokens + (now - lastRefill) / NANOS_PER_SECOND * ratePerSecond);
        lastRefill = now;
        if (tokens >= 1) {
            tokens -= 1;
            return 0;
        }
        return (long) Math.ceil((1 - tokens) / ratePerSecond);
    }
}
