package com.edge.app.application.vote;

import com.edge.app.application.vote.required.VoteCountStore;
import com.edge.app.application.vote.required.VoteRepository;
import com.edge.app.domain.vote.VotesRemoved;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;
import org.springframework.transaction.event.TransactionalEventListener;

@Slf4j
@Component
@RequiredArgsConstructor
public class VoteCacheListener {
    private final VoteCountStore voteCountStore;
    private final VoteRepository voteRepository;

    // 남은 표 기준의 집계 교체
    // 실패의 정합 주기 위임
    @TransactionalEventListener
    public void replaceInCache(VotesRemoved event) {
        for (String etfCode : event.etfCodes()) {
            try {
                voteCountStore.replace(etfCode, voteRepository.findByEtfCode(etfCode));
            } catch (Exception ex) {
                log.warn("Vote cache replace failed etf={}", etfCode, ex);
            }
        }
    }
}
