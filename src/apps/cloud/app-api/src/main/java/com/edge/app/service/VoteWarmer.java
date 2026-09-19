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
import org.springframework.stereotype.Component;
import reactor.core.Disposable;

import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
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
    private Disposable subscription;

    @PostConstruct
    void subscribe() {
        subscription = clientResources.eventBus().get()
                .filter(event -> event instanceof ConnectionActivatedEvent)
                .subscribe(event -> executor.submit(this::warm));
    }

    @EventListener(ApplicationReadyEvent.class)
    void onReady() {
        executor.submit(this::warm);
    }

    public void warm() {
        try {
            Map<Long, List<Vote>> byForecast = voteRepository.findAll().stream()
                    .collect(Collectors.groupingBy(Vote::getForecastId));
            long loaded = byForecast.entrySet().stream()
                    .filter(entry -> buffer.loadIfAbsent(entry.getKey(), entry.getValue())).count();
            meterRegistry.counter("vote.warm.loaded").increment(loaded);
            log.info("Warm finished forecasts={} loaded={}", byForecast.size(), loaded);
        } catch (Exception ex) {
            meterRegistry.counter("vote.warm.failures").increment();
            log.warn("Warm failed; next trigger will retry", ex);
        }
    }

    @PreDestroy
    void close() {
        subscription.dispose();
        executor.shutdownNow();
    }
}
