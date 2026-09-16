package com.edge.app.service;

import com.edge.app.dto.VoteCountResponse;
import com.edge.app.dto.VoteCounts;
import com.edge.app.entity.VoteChoice;
import com.edge.app.event.VoteRecorded;
import com.edge.app.repository.VoteCountRepository;
import com.edge.app.repository.VoteRepository;
import io.github.resilience4j.circuitbreaker.annotation.CircuitBreaker;
import io.micrometer.core.instrument.MeterRegistry;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.EnumMap;
import java.util.Map;

@Slf4j
@Service
@RequiredArgsConstructor
public class VoteService {
    private final VoteRepository voteRepository;
    private final VoteCountRepository voteCountRepository;
    private final MeterRegistry meterRegistry;
    private final ApplicationEventPublisher eventPublisher;

    // Redis 갱신은 커밋 이후여야 한다 — 트랜잭션 안에서 이벤트만 발행하고,
    // VoteCacheListener(AFTER_COMMIT)가 캐시를 따라 갱신한다(실패는 repository 폴백이 삼킴).
    @Transactional
    public void vote(Long forecastId, Long userId, VoteChoice choice) {
        voteRepository.upsert(forecastId, userId, choice.name());
        eventPublisher.publishEvent(new VoteRecorded(forecastId, userId, choice));
    }

    // source(redis/db)는 어느 경로로 읽었는지의 표식 — 폴백 정책을 아는 이 계층이 붙인다.
    @CircuitBreaker(name = "redis", fallbackMethod = "countsFromDb")
    public VoteCountResponse counts(Long forecastId) {
        return VoteCountResponse.from(voteCountRepository.counts(forecastId), "redis");
    }

    private VoteCountResponse countsFromDb(Long forecastId, Throwable ex) {
        meterRegistry.counter("vote.redis.read.failures").increment();
        log.warn("Redis count failed forecast={}; using DB", forecastId, ex);
        Map<VoteChoice, Long> counts = new EnumMap<>(VoteChoice.class);
        voteRepository.countByChoice(forecastId).forEach(row -> counts.put(row.getChoice(), row.getTotal()));
        return VoteCountResponse.from(new VoteCounts(counts.getOrDefault(VoteChoice.BUY, 0L),
                counts.getOrDefault(VoteChoice.HOLD, 0L),
                counts.getOrDefault(VoteChoice.SELL, 0L)), "db");
    }
}
