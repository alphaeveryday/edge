package com.edge.app.community.vote.controller;

import com.edge.app.community.vote.service.VoteService;

import com.edge.app.common.auth.MemberPrincipal;
import com.edge.app.community.vote.dto.VoteCountResponse;
import com.edge.app.community.vote.dto.VoteRequest;
import com.edge.common.apipayload.ApiResponse;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/etfs/{code}/vote")
@RequiredArgsConstructor
public class VoteController {
    private final VoteService voteService;

    // 응답의 현황 제외
    // 쓰기 경로에 읽기가 붙으면 달라지는 장애 실험 조건
    // 앱의 성공 후 count 조회
    @PutMapping
    public ApiResponse<Void> communityVote(@PathVariable String code, MemberPrincipal principal,
            @RequestBody @Valid VoteRequest request) {
        voteService.vote(code, principal.memberId(), request.choice());
        return ApiResponse.onSuccess(null);
    }

    @GetMapping("/count")
    public ApiResponse<VoteCountResponse> communityVoteCount(@PathVariable String code) {
        return ApiResponse.onSuccess(voteService.counts(code));
    }
}
