package com.edge.app.service;

import com.edge.app.entity.VoteChoice;
import com.edge.app.event.VoteRecorded;
import com.edge.app.repository.VoteRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
@ConditionalOnProperty(name = "vote.mode", havingValue = "db-first", matchIfMissing = true)
@RequiredArgsConstructor
public class DbFirstVoteCommandService implements VoteCommandService {
    private final VoteRepository voteRepository;
    private final ApplicationEventPublisher eventPublisher;

    // Redis 갱신은 커밋 이후여야 한다 — 트랜잭션 안에서 이벤트만 발행하고,
    // VoteCacheListener(AFTER_COMMIT)가 캐시를 따라 갱신한다(실패는 repository 폴백이 삼킴).
    @Override
    @Transactional
    public void vote(Long forecastId, Long userId, VoteChoice choice) {
        voteRepository.upsert(forecastId, userId, choice.name());
        eventPublisher.publishEvent(new VoteRecorded(forecastId, userId, choice));
    }
}
