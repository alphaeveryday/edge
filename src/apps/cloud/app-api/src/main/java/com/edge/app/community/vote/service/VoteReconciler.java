package com.edge.app.community.vote.service;

import com.edge.app.community.vote.entity.Vote;
import com.edge.app.community.vote.repository.VoteCountRepository;
import com.edge.app.community.vote.repository.VoteRepository;

import io.lettuce.core.event.connection.ConnectionActivatedEvent;
import io.lettuce.core.resource.ClientResources;
import io.micrometer.core.instrument.MeterRegistry;
import jakarta.annotation.PostConstruct;
import jakarta.annotation.PreDestroy;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import net.javacrumbs.shedlock.spring.annotation.SchedulerLock;
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
    @SchedulerLock(name = "vote-reconcile", lockAtMostFor = "PT4M")
    public void scheduled() {
        request();
    }

    // 겹친 트리거 병합
    // 블로킹 작업을 막는 Lettuce 이벤트 스레드의 제출 한정
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
            // 목록 조회와 전망별 조회의 시차를 없애는 단일 findAll 스냅샷 기준 교체
            Map<String, List<Vote>> byEtf = voteRepository.findAll().stream()
                    .collect(Collectors.groupingBy(Vote::getEtfCode));
            // 전망 단위 격리
            // Cluster 부분 장애에서 한 샤드 실패의 정상 샤드 전망 복구 차단 방지
            int failed = 0;
            for (var entry : byEtf.entrySet()) {
                try {
                    var delta = voteCountRepository.replace(entry.getKey(), entry.getValue());
                    meterRegistry.counter("vote.reconcile.missing").increment(delta.missing());
                    meterRegistry.counter("vote.reconcile.excess").increment(delta.excess());
                    log.info("Reconciled etf={} dbVotes={} buyDelta={} waitDelta={} sellDelta={}",
                            entry.getKey(), entry.getValue().size(), delta.buyDelta(), delta.waitDelta(), delta.sellDelta());
                } catch (Exception ex) {
                    failed++;
                    log.warn("Reconciliation failed etf={}; next trigger will repair", entry.getKey(), ex);
                }
            }
            meterRegistry.counter(failed == 0 ? "vote.reconcile.success" : "vote.reconcile.failures").increment();
            if (failed > 0) {
                log.warn("Reconciliation partial: {} of {} etfs failed", failed, byEtf.size());
            }
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
