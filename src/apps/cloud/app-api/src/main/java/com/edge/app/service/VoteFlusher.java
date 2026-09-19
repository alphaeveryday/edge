package com.edge.app.service;

import com.edge.app.entity.VoteChoice;
import com.edge.app.repository.VoteBufferRepository;
import com.edge.app.repository.VoteFlushRepository;
import io.micrometer.core.instrument.Gauge;
import io.micrometer.core.instrument.MeterRegistry;
import jakarta.annotation.PostConstruct;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.Map;
import java.util.concurrent.TimeUnit;

@Slf4j
@Component
@ConditionalOnProperty(name = "vote.mode", havingValue = "write-behind")
@RequiredArgsConstructor
public class VoteFlusher {
    private final VoteBufferRepository buffer;
    private final VoteFlushRepository flushRepository;
    private final MeterRegistry meterRegistry;

    @Value("${vote.flush.batch-size:500}")
    private int batchSize;

    @PostConstruct
    void registerGauge() {
        Gauge.builder("vote.dirty.size", buffer, b -> b.dirtyForecasts().stream().mapToLong(b::dirtySize).sum())
                .register(meterRegistry);
    }

    @Scheduled(fixedDelayString = "${vote.flush.interval-ms:3000}", initialDelayString = "${vote.flush.interval-ms:3000}")
    public void flush() {
        long start = System.nanoTime();
        try {
            for (Long forecastId : buffer.dirtyForecasts()) {
                flushForecast(forecastId);
            }
        } catch (Exception ex) {
            meterRegistry.counter("vote.flush.failures").increment();
            log.warn("Flush run failed before any forecast; retry next run", ex);
        } finally {
            meterRegistry.timer("vote.flush.duration").record(System.nanoTime() - start, TimeUnit.NANOSECONDS);
        }
    }

    // 전망 하나의 실패(DB 장애·손상 dirty)가 다른 전망의 flush 를 막지 않도록 전망 단위로 격리한다.
    private void flushForecast(Long forecastId) {
        try {
            Map<Long, VoteChoice> batch = buffer.readDirty(forecastId, batchSize);
            flushRepository.upsertAll(forecastId, batch);
            batch.forEach((userId, choice) -> buffer.clearDirtyIfUnchanged(forecastId, userId, choice));
            buffer.releaseIfClean(forecastId);
            meterRegistry.counter("vote.flush.size").increment(batch.size());
        } catch (Exception ex) {
            meterRegistry.counter("vote.flush.failures").increment();
            log.warn("Flush failed forecast={}; dirty votes retry next run", forecastId, ex);
        }
    }
}
