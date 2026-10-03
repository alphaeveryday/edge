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
    private final RedisCircuit circuit;

    // 커밋 이후의 Redis 갱신
    // 트랜잭션 안에서의 이벤트 발행 한정
    // AFTER_COMMIT 단계 VoteCacheListener 의 캐시 갱신
    // 갱신 실패의 repository 폴백 흡수
    @Transactional
    public void vote(String etfCode, Long memberId, VoteChoice choice) {
        voteRepository.upsert(etfCode, memberId, choice.value());
        eventPublisher.publishEvent(new VoteRecorded(etfCode, memberId, choice));
    }

    // 읽은 경로를 나타내는 source 표식
    // 폴백 정책을 아는 이 계층의 표식 부착
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
