package com.edge.app.application.vote.required;

import com.edge.app.domain.vote.Vote;
import com.edge.app.domain.vote.VoteChoice;
import com.edge.app.domain.vote.VoteCounts;

import java.util.List;

/**
 * 투표 집계 저장소
 * - 쓰기 실패의 예외 없는 흡수
 * - 읽기 실패와 열린 서킷의 예외 전파
 * - 재조정 replace 의 서킷 제외
 */
public interface VoteCountStore {
    void vote(String etfCode, Long memberId, VoteChoice choice);

    void withdraw(String etfCode, Long memberId);

    VoteCounts counts(String etfCode);

    VoteReconcileResult replace(String etfCode, List<Vote> votes);
}
