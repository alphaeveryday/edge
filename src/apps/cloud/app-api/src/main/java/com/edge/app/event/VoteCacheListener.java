package com.edge.app.event;

import com.edge.app.repository.VoteCountRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;
import org.springframework.transaction.event.TransactionalEventListener;

@Component
@RequiredArgsConstructor
public class VoteCacheListener {
    private final VoteCountRepository voteCountRepository;

    @TransactionalEventListener
    public void applyToCache(VoteRecorded event) {
        voteCountRepository.vote(event.forecastId(), event.userId(), event.choice());
    }
}
