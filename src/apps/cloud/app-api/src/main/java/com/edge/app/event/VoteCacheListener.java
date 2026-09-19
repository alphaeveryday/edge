package com.edge.app.event;

import com.edge.app.repository.VoteCountRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;
import org.springframework.transaction.event.TransactionalEventListener;

@Component
@ConditionalOnProperty(name = "vote.mode", havingValue = "db-first", matchIfMissing = true)
@RequiredArgsConstructor
public class VoteCacheListener {
    private final VoteCountRepository voteCountRepository;

    @TransactionalEventListener
    public void applyToCache(VoteRecorded event) {
        voteCountRepository.vote(event.forecastId(), event.userId(), event.choice());
    }
}
