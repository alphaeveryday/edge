package com.edge.app.service;

import com.edge.app.entity.Vote;
import com.edge.app.repository.VoteCountRepository;
import com.edge.app.repository.VoteRepository;
import io.lettuce.core.event.connection.ConnectionActivatedEvent;
import io.lettuce.core.resource.ClientResources;
import io.micrometer.core.instrument.MeterRegistry;
import jakarta.annotation.PostConstruct;
import jakarta.annotation.PreDestroy;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import reactor.core.Disposable;

import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.stream.Collectors;

@Slf4j
@Component
@ConditionalOnProperty(name = "vote.mode", havingValue = "db-first", matchIfMissing = true)
@RequiredArgsConstructor
public class VoteReconciler {
    private final VoteRepository voteRepository;
    private final VoteCountRepository voteCountRepository;
    private final MeterRegistry meterRegistry;
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final AtomicBoolean running = new AtomicBoolean();
    private final ClientResources clientResources;
    private Disposable subscription;

    @PostConstruct
    public void subscribe() {
        subscription = clientResources.eventBus().get()
                .filter(event -> event instanceof ConnectionActivatedEvent)
                .subscribe(event -> request());
    }

    @Scheduled(fixedDelayString = "${vote.reconcile.interval:PT5M}", initialDelayString = "${vote.reconcile.initial-delay:PT1S}")
    public void scheduled() {
        request();
    }

    // 겹친 트리거는 병합하고, Lettuce 이벤트 스레드에서는 제출만 한다(블로킹 작업 금지).
    public boolean request() {
        if (!running.compareAndSet(false, true)) {
            return false;
        }
        executor.submit(this::reconcile);
        return true;
    }

    private void reconcile() {
        long start = System.nanoTime();
        try {
            // 단일 findAll 스냅샷 — 전망 목록/전망별 조회의 시차 없이 한 시점 기준으로 교체한다.
            Map<Long, List<Vote>> byForecast = voteRepository.findAll().stream()
                    .collect(Collectors.groupingBy(Vote::getForecastId));
            for (var entry : byForecast.entrySet()) {
                var delta = voteCountRepository.replace(entry.getKey(), entry.getValue());
                meterRegistry.counter("vote.reconcile.missing").increment(delta.missing());
                meterRegistry.counter("vote.reconcile.excess").increment(delta.excess());
                log.info("Reconciled forecast={} dbVotes={} buyDelta={} holdDelta={} sellDelta={}",
                        entry.getKey(), entry.getValue().size(), delta.buyDelta(), delta.holdDelta(), delta.sellDelta());
            }
            meterRegistry.counter("vote.reconcile.success").increment();
        } catch (Exception ex) {
            meterRegistry.counter("vote.reconcile.failures").increment();
            log.warn("Reconciliation failed; next trigger will repair", ex);
        } finally {
            long elapsed = System.nanoTime() - start;
            meterRegistry.timer("vote.reconcile.duration").record(elapsed, TimeUnit.NANOSECONDS);
            log.info("Reconciliation finished elapsedMs={}", elapsed / 1_000_000);
            running.set(false);
        }
    }

    @PreDestroy
    public void close() {
        subscription.dispose();
        executor.shutdownNow();
    }
}
