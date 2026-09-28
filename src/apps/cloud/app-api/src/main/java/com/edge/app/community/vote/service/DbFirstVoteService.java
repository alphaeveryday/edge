package com.edge.app.community.vote.service;

import com.edge.app.community.vote.entity.VoteChoice;
import com.edge.app.community.vote.event.VoteCacheListener;
import com.edge.app.community.vote.event.VoteRecorded;
import com.edge.app.community.vote.repository.VoteCountRepository;
import com.edge.app.community.vote.repository.VoteRepository;

import com.edge.app.community.vote.dto.VoteCountResponse;
import com.edge.app.community.vote.repository.VoteCounts;
import com.edge.app.common.config.RedisCircuit;
import io.micrometer.core.instrument.MeterRegistry;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.EnumMap;
import java.util.Map;

@Slf4j
@Service
@ConditionalOnProperty(name = "vote.mode", havingValue = "db-first", matchIfMissing = true)
@RequiredArgsConstructor
public class DbFirstVoteService implements VoteService {
    private final VoteRepository voteRepository;
    private final VoteCountRepository voteCountRepository;
    private final MeterRegistry meterRegistry;
    private final ApplicationEventPublisher eventPublisher;
    private final RedisCircuit circuit;

    // Redis 갱신은 커밋 이후여야 한다 — 트랜잭션 안에서 이벤트만 발행하고,
    // VoteCacheListener(AFTER_COMMIT)가 캐시를 따라 갱신한다(실패는 repository 폴백이 삼킴).
    @Override
    @Transactional
    public void vote(String etfCode, Long memberId, VoteChoice choice) {
        voteRepository.upsert(etfCode, memberId, choice.value());
        eventPublisher.publishEvent(new VoteRecorded(etfCode, memberId, choice));
    }

    // source(redis/db)는 어느 경로로 읽었는지의 표식 — 폴백 정책을 아는 이 계층이 붙인다.
    @Override
    public VoteCountResponse counts(String etfCode) {
        try {
            return circuit.of(etfCode).executeSupplier(
                    () -> VoteCountResponse.from(voteCountRepository.counts(etfCode), "redis"));
        } catch (Exception ex) {
            return countsFromDb(etfCode, ex);
        }
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
