package com.edge.app.community.vote.writebehind;

import com.edge.app.community.vote.dto.VoteCountResponse;
import com.edge.app.community.vote.dto.VoteCounts;
import com.edge.app.community.vote.VoteChoice;
import com.edge.app.common.AppErrorStatus;
import com.edge.app.community.vote.VoteCountRepository;
import com.edge.app.community.vote.VoteRepository;
import com.edge.app.community.vote.VoteService;
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
    public void vote(String etfCode, Long memberId, VoteChoice choice) {
        buffer.record(etfCode, memberId, choice);
    }

    private void onRedisDown(String etfCode, Long memberId, VoteChoice choice, Throwable ex) {
        meterRegistry.counter("vote.redis.write.failures").increment();
        log.warn("Redis vote failed etf={}; rejected (write-behind)", etfCode, ex);
        throw new GeneralException(AppErrorStatus.VOTE_STORE_UNAVAILABLE);
    }

    // DB 폴백은 flush 지연분만큼 낡은 값이다.
    @Override
    @CircuitBreaker(name = "redis", fallbackMethod = "countsFromDb")
    public VoteCountResponse counts(String etfCode) {
        return VoteCountResponse.from(voteCountRepository.counts(etfCode), "redis");
    }

    private VoteCountResponse countsFromDb(String etfCode, Throwable ex) {
        meterRegistry.counter("vote.redis.read.failures").increment();
        log.warn("Redis count failed etf={}; using DB", etfCode, ex);
        Map<VoteChoice, Long> counts = new EnumMap<>(VoteChoice.class);
        voteRepository.countByChoice(etfCode).forEach(row -> counts.put(row.getChoice(), row.getTotal()));
        return VoteCountResponse.from(new VoteCounts(counts.getOrDefault(VoteChoice.BUY, 0L),
                counts.getOrDefault(VoteChoice.WAIT, 0L),
                counts.getOrDefault(VoteChoice.SELL, 0L)), "db");
    }
}
