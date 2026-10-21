package com.edge.app.adapter.webapi.vote;

import com.edge.app.adapter.security.MemberPrincipal;
import com.edge.app.adapter.webapi.vote.dto.MyVoteResponse;
import com.edge.app.adapter.webapi.vote.dto.VoteRequest;
import com.edge.app.application.vote.provided.VoteCountResponse;
import com.edge.app.application.vote.provided.VoteFinder;
import com.edge.app.application.vote.provided.VoteModifier;
import com.edge.common.apipayload.ApiResponse;

import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.DeleteMapping;
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
    private final VoteModifier voteModifier;
    private final VoteFinder voteFinder;

    // 응답의 현황 제외
    // 쓰기 경로에 읽기가 붙으면 달라지는 장애 실험 조건
    // 앱의 성공 후 count 조회
    @PutMapping
    public ApiResponse<Void> communityVote(@PathVariable String code, MemberPrincipal principal,
            @RequestBody @Valid VoteRequest request) {
        voteModifier.vote(code, principal.memberId(), request.choice());
        return ApiResponse.onSuccess(null);
    }

    @DeleteMapping
    public ApiResponse<Void> communityVoteWithdraw(@PathVariable String code, MemberPrincipal principal) {
        voteModifier.withdraw(code, principal.memberId());
        return ApiResponse.onSuccess(null);
    }

    @GetMapping
    public ApiResponse<MyVoteResponse> communityMyVote(@PathVariable String code, MemberPrincipal principal) {
        return ApiResponse.onSuccess(new MyVoteResponse(voteFinder.myVote(code, principal.memberId()).orElse(null)));
    }

    @GetMapping("/count")
    public ApiResponse<VoteCountResponse> communityVoteCount(@PathVariable String code) {
        return ApiResponse.onSuccess(voteFinder.counts(code));
    }
}
