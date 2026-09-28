package com.edge.app.service;

import com.edge.app.dto.VoteCountResponse;
import com.edge.app.entity.VoteChoice;

// 쓰기 정책별 구현(db-first / write-behind)을 vote.mode 로 택일한다 — 실험용, 종료 후 한쪽만 남긴다.
public interface VoteService {
    void vote(String etfCode, Long memberId, VoteChoice choice);

    VoteCountResponse counts(String etfCode);
}
