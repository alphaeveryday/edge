package com.edge.app.community.vote.event;

import com.edge.app.community.vote.repository.VoteCountRepository;
import com.edge.app.community.vote.repository.VoteRepository;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;
import org.springframework.transaction.event.TransactionalEventListener;

@Slf4j
@Component
@RequiredArgsConstructor
public class VoteCacheListener {
    private final VoteCountRepository voteCountRepository;
    private final VoteRepository voteRepository;

    // 남은 표 기준의 집계 교체
    // 실패의 정합 주기 위임
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
