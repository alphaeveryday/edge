package com.edge.app.application.vote.provided;

import com.edge.app.domain.vote.VoteChoice;

/**
 * 투표와 철회
 * - DB 커밋 뒤 집계 반영
 */
public interface VoteModifier {
    void vote(String etfCode, Long memberId, VoteChoice choice);

    void withdraw(String etfCode, Long memberId);
}
