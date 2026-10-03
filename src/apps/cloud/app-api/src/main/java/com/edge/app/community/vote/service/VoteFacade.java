package com.edge.app.community.vote.service;

import com.edge.app.community.vote.entity.VoteChoice;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

// 트랜잭션 밖의 순서 조율
// DB 커밋과 커넥션 반납 이후의 Redis 반영
@Service
@RequiredArgsConstructor
public class VoteFacade {
    private final VoteService voteService;

    public void vote(String etfCode, Long memberId, VoteChoice choice) {
        voteService.vote(etfCode, memberId, choice);
        voteService.updateCount(etfCode, memberId, choice);
    }
}
