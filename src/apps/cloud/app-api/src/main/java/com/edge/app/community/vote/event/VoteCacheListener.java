package com.edge.app.community.vote.event;

import com.edge.app.community.vote.repository.VoteCountRepository;
import com.edge.app.community.vote.repository.VoteRepository;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;
import org.springframework.transaction.event.TransactionalEventListener;

@Slf4j
@Component
@ConditionalOnProperty(name = "vote.mode", havingValue = "db-first", matchIfMissing = true)
@RequiredArgsConstructor
public class VoteCacheListener {
    private final VoteCountRepository voteCountRepository;
    private final VoteRepository voteRepository;

    @TransactionalEventListener
    public void applyToCache(VoteRecorded event) {
        voteCountRepository.vote(event.etfCode(), event.memberId(), event.choice());
    }

    // 남은 표로 집계 교체. 실패는 정합 주기에 맡김
    @TransactionalEventListener
    public void replaceInCache(VotesRemoved event) {
        for (String etfCode : event.etfCodes()) {
            try {
                voteCountRepository.replace(etfCode, voteRepository.findByEtfCode(etfCode));
            } catch (Exception ex) {
                log.warn("Vote cache replace failed etf={}", etfCode, ex);
            }
        }
    }
}
