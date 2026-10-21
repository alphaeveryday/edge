package com.edge.app.application.vote.provided;

import com.edge.app.domain.vote.VoteChoice;

import java.util.Optional;

/**
 * 내 투표와 집계 조회
 * - 집계 읽기 실패 시 DB 집계 폴백
 */
public interface VoteFinder {
    Optional<VoteChoice> myVote(String etfCode, Long memberId);

    VoteCountResponse counts(String etfCode);
}
