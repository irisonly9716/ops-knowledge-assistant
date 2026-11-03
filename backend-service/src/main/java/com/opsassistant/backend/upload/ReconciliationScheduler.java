package com.opsassistant.backend.upload;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.annotation.EnableScheduling;
import org.springframework.scheduling.annotation.Scheduled;

/** Runs the reconciliation job. Switched off with reconciliation.enabled=false (tests call it directly instead). */
@Slf4j
@Configuration(proxyBeanMethods = false)
@EnableScheduling
@RequiredArgsConstructor
@ConditionalOnProperty(name = "reconciliation.enabled", havingValue = "true", matchIfMissing = true)
public class ReconciliationScheduler {

    private final UploadReconciler reconciler;

    @Scheduled(initialDelayString = "${reconciliation.interval-ms}", fixedDelayString = "${reconciliation.interval-ms}")
    public void run() {
        try {
            reconciler.reconcile();
        } catch (RuntimeException e) {   // the database is unreachable, for example: the next run tries again
            log.warn("reconciliation: run failed: {}", e.toString());
        }
    }
}
