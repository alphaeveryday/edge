package com.edge.app.adapter.webapi.member;

import com.edge.app.adapter.security.MemberPrincipal;
import com.edge.app.application.member.MemberService;
import com.edge.app.application.member.provided.MeResponse;
import com.edge.app.application.member.provided.MeUpdateRequest;
import com.edge.common.apipayload.ApiResponse;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/me")
@RequiredArgsConstructor
public class MemberController {
    private final MemberService memberService;

    @GetMapping
    public ApiResponse<MeResponse> memberMe(MemberPrincipal principal) {
        return ApiResponse.onSuccess(memberService.me(principal.memberId()));
    }

    @PatchMapping
    public ApiResponse<MeResponse> memberUpdate(MemberPrincipal principal, @RequestBody @Valid MeUpdateRequest request) {
        return ApiResponse.onSuccess(memberService.update(principal.memberId(), request));
    }

    @DeleteMapping
    public ApiResponse<Void> memberDeleteAccount(MemberPrincipal principal) {
        memberService.deleteAccount(principal.memberId());
        return ApiResponse.onSuccess(null);
    }

    @PostMapping("/disclaimer")
    public ApiResponse<MeResponse> memberAcceptDisclaimer(MemberPrincipal principal) {
        return ApiResponse.onSuccess(memberService.acceptDisclaimer(principal.memberId()));
    }
}
