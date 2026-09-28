package com.edge.app.community.vote;

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

    // 응답에 현황을 싣지 않는다. 쓰기 경로에 읽기를 붙이면 장애 실측 조건이 달라진다. 앱은 성공 후 count 를 읽는다.
    @PutMapping
    public ApiResponse<Void> vote(@PathVariable String code, MemberPrincipal principal,
            @RequestBody @Valid VoteRequest request) {
        voteService.vote(code, principal.memberId(), request.choice());
        return ApiResponse.onSuccess(null);
    }

    @GetMapping("/count")
    public ApiResponse<VoteCountResponse> counts(@PathVariable String code) {
        return ApiResponse.onSuccess(voteService.counts(code));
    }
}
