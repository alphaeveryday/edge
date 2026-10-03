package com.edge.app.community.vote.service;

import com.edge.app.community.vote.entity.VoteChoice;

import com.edge.app.community.vote.dto.VoteCountResponse;

// vote.mode 기반의 db-first 와 write-behind 구현 택일
// 실험 종료 후 한쪽 구현의 단독 유지
public interface VoteService {
    void vote(String etfCode, Long memberId, VoteChoice choice);

    VoteCountResponse counts(String etfCode);
}
