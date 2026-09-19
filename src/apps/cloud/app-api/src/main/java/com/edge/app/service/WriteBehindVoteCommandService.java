package com.edge.app.service;

import com.edge.app.entity.VoteChoice;
import com.edge.app.error.AppErrorStatus;
import com.edge.app.repository.VoteBufferRepository;
import com.edge.common.exception.GeneralException;
import io.github.resilience4j.circuitbreaker.annotation.CircuitBreaker;
import io.micrometer.core.instrument.MeterRegistry;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Service;

@Slf4j
@Service
@ConditionalOnProperty(name = "vote.mode", havingValue = "write-behind")
@RequiredArgsConstructor
public class WriteBehindVoteCommandService implements VoteCommandService {
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
}
