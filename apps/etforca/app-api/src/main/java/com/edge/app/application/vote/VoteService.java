package com.edge.app.application.vote;

import com.edge.app.application.vote.provided.VoteCountResponse;
import com.edge.app.application.vote.provided.VoteFinder;
import com.edge.app.application.vote.required.VoteCountStore;
import com.edge.app.application.vote.required.VoteRepository;
import com.edge.app.domain.vote.Vote;
import com.edge.app.domain.vote.VoteChoice;
import com.edge.app.domain.vote.VoteCounts;

import io.micrometer.core.instrument.MeterRegistry;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.EnumMap;
import java.util.Map;
import java.util.Optional;

@Slf4j
@Service
@RequiredArgsConstructor
public class VoteService implements VoteFinder {
    private final VoteRepository voteRepository;
    private final VoteCountStore voteCountStore;
    private final MeterRegistry meterRegistry;

    @Transactional
    public void vote(String etfCode, Long memberId, VoteChoice choice) {
        voteRepository.upsert(etfCode, memberId, choice.value());
    }

    // 트랜잭션 없는 Redis 반영
    // 갱신 실패의 repository 폴백 흡수
    public void updateCount(String etfCode, Long memberId, VoteChoice choice) {
        voteCountStore.vote(etfCode, memberId, choice);
    }

    @Transactional
    public void withdraw(String etfCode, Long memberId) {
        voteRepository.deleteByEtfCodeAndMemberId(etfCode, memberId);
    }

    public void removeFromCount(String etfCode, Long memberId) {
        voteCountStore.withdraw(etfCode, memberId);
    }

    // 읽은 경로를 나타내는 source 표식
    // 폴백 정책을 아는 이 계층의 표식 부착
    @Override
    public VoteCountResponse counts(String etfCode) {
        try {
            return VoteCountResponse.from(voteCountStore.counts(etfCode), "redis");
        } catch (Exception ex) {
            return countsFromDb(etfCode, ex);
        }
    }

    @Override
    public Optional<VoteChoice> myVote(String etfCode, Long memberId) {
        return voteRepository.findByEtfCodeAndMemberId(etfCode, memberId).map(Vote::getChoice);
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
