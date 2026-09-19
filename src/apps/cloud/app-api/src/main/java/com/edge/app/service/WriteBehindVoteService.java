package com.edge.app.service;

import com.edge.app.entity.VoteChoice;
import com.edge.app.error.AppErrorStatus;
import com.edge.app.repository.VoteBufferRepository;
import com.edge.app.repository.VoteCountRepository;
import com.edge.app.repository.VoteRepository;
import com.edge.common.exception.GeneralException;
import io.github.resilience4j.circuitbreaker.annotation.CircuitBreaker;
import io.micrometer.core.instrument.MeterRegistry;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.context.annotation.Primary;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

@Slf4j
@Service
@Primary
@ConditionalOnProperty(name = "vote.mode", havingValue = "write-behind")
public class WriteBehindVoteService extends VoteService {
    private final VoteBufferRepository buffer;
    private final MeterRegistry meterRegistry;

    public WriteBehindVoteService(VoteRepository voteRepository, VoteCountRepository voteCountRepository,
            MeterRegistry meterRegistry, ApplicationEventPublisher eventPublisher, VoteBufferRepository buffer) {
        super(voteRepository, voteCountRepository, meterRegistry, eventPublisher);
        this.buffer = buffer;
        this.meterRegistry = meterRegistry;
    }

    @Override
    @Transactional(propagation = Propagation.NOT_SUPPORTED)
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
