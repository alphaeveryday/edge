package com.edge.app.service;

import com.edge.app.entity.Vote;
import com.edge.app.repository.VoteBufferRepository;
import com.edge.app.repository.VoteRepository;
import io.lettuce.core.event.connection.ConnectionActivatedEvent;
import io.lettuce.core.resource.ClientResources;
import io.micrometer.core.instrument.MeterRegistry;
import jakarta.annotation.PostConstruct;
import jakarta.annotation.PreDestroy;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import reactor.core.Disposable;

import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.stream.Collectors;

@Slf4j
@Component
@ConditionalOnProperty(name = "vote.mode", havingValue = "write-behind")
@RequiredArgsConstructor
public class VoteWarmer {
    private final VoteRepository voteRepository;
    private final VoteBufferRepository buffer;
    private final MeterRegistry meterRegistry;
    private final ClientResources clientResources;
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final AtomicBoolean running = new AtomicBoolean();
    private Disposable subscription;

    @PostConstruct
    void subscribe() {
        subscription = clientResources.eventBus().get()
                .filter(event -> event instanceof ConnectionActivatedEvent)
                .subscribe(event -> request());
    }

    @EventListener(ApplicationReadyEvent.class)
    void onReady() {
        request();
    }

    // 겹친 트리거는 병합하고, Lettuce 이벤트 스레드에서는 제출만 한다(블로킹 작업 금지).
    public boolean request() {
        if (!running.compareAndSet(false, true)) {
            return false;
        }
        executor.submit(this::warm);
        return true;
    }

    // 재연결 없이 warm 만 실패한 경우(일시 DB 장애)를 위한 주기 재시도.
    @Scheduled(fixedDelayString = "${vote.warm.interval:PT5M}", initialDelayString = "${vote.warm.interval:PT5M}")
    void scheduled() {
        request();
    }

    public void warm() {
        try {
            Map<Long, List<Vote>> byForecast = voteRepository.findAll().stream()
                    .collect(Collectors.groupingBy(Vote::getForecastId));
            long loaded = byForecast.entrySet().stream()
                    .mapToLong(entry -> buffer.mergeMissing(entry.getKey(), entry.getValue())).sum();
            meterRegistry.counter("vote.warm.loaded").increment(loaded);
            log.info("Warm finished forecasts={} loaded={}", byForecast.size(), loaded);
        } catch (Exception ex) {
            meterRegistry.counter("vote.warm.failures").increment();
            log.warn("Warm failed; next trigger will retry", ex);
        } finally {
            running.set(false);
        }
    }

    @PreDestroy
    void close() {
        subscription.dispose();
        executor.shutdownNow();
    }
}
