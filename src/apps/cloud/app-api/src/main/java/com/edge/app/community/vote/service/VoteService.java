package com.edge.app.community.vote.service;

import com.edge.app.community.vote.entity.VoteChoice;

import com.edge.app.community.vote.dto.VoteCountResponse;

// 쓰기 정책별 구현(db-first / write-behind)을 vote.mode 로 택일한다 — 실험용, 종료 후 한쪽만 남긴다.
public interface VoteService {
    void vote(String etfCode, Long memberId, VoteChoice choice);

    VoteCountResponse counts(String etfCode);
}
