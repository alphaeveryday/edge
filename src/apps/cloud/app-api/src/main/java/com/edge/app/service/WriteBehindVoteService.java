package com.edge.app.service;

import com.edge.app.repository.VoteCountRepository;
import com.edge.app.repository.VoteRepository;
import io.micrometer.core.instrument.MeterRegistry;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.context.annotation.Primary;
import org.springframework.stereotype.Service;

@Service
@Primary
@ConditionalOnProperty(name = "vote.mode", havingValue = "write-behind")
public class WriteBehindVoteService extends VoteService {
    public WriteBehindVoteService(VoteRepository voteRepository, VoteCountRepository voteCountRepository,
            MeterRegistry meterRegistry, ApplicationEventPublisher eventPublisher) {
        super(voteRepository, voteCountRepository, meterRegistry, eventPublisher);
    }
}
