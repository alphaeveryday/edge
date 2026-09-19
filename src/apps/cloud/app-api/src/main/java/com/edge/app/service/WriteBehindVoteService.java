package com.edge.app.service;

import com.edge.app.dto.VoteCountResponse;
import com.edge.app.dto.VoteCounts;
import com.edge.app.entity.VoteChoice;
import com.edge.app.error.AppErrorStatus;
import com.edge.app.repository.VoteBufferRepository;
import com.edge.app.repository.VoteCountRepository;
import com.edge.app.repository.VoteRepository;
import com.edge.common.exception.GeneralException;
import io.github.resilience4j.circuitbreaker.annotation.CircuitBreaker;
import io.micrometer.core.instrument.MeterRegistry;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Service;

import java.util.EnumMap;
import java.util.Map;

@Slf4j
@Service
@ConditionalOnProperty(name = "vote.mode", havingValue = "write-behind")
@RequiredArgsConstructor
public class WriteBehindVoteService implements VoteService {
    private final VoteRepository voteRepository;
    private final VoteCountRepository voteCountRepository;
    private final VoteBufferRepository buffer;
    private final MeterRegistry meterRegistry;

    // DB 트랜잭션 없이 Redis 에만 기록한다. Redis 실패는 DB 우회 없이 즉시 반려.
    @Override
    @CircuitBreaker(name = "redis", fallbackMethod = "onRedisDown")
    public void vote(Long forecastId, Long userId, VoteChoice choice) {
        buffer.record(forecastId, userId, choice);
    }

    private void onRedisDown(Long forecastId, Long userId, VoteChoice choice, Throwable ex) {
        meterRegistry.counter("vote.redis.write.failures").increment();
        log.warn("Redis vote failed forecast={}; rejected (write-behind)", forecastId, ex);
        throw new GeneralException(AppErrorStatus.VOTE_STORE_UNAVAILABLE);
    }

    // DB 폴백은 flush 지연분만큼 낡은 값이다.
    @Override
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
