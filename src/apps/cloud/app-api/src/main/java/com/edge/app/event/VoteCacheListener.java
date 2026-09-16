package com.edge.app.event;

import com.edge.app.repository.VoteCountRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;
import org.springframework.transaction.event.TransactionalEventListener;

@Component
@RequiredArgsConstructor
public class VoteCacheListener {
    private final VoteCountRepository voteCountRepository;

    // AFTER_COMMIT(기본 phase) — 커밋 전 캐시 갱신·롤백 시 유령 표가 구조적으로 불가능하다.
    @TransactionalEventListener
    public void applyToCache(VoteRecorded event) {
        voteCountRepository.vote(event.forecastId(), event.userId(), event.choice());
    }
}
