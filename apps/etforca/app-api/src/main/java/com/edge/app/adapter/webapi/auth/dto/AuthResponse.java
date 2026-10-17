package com.edge.app.adapter.webapi.auth.dto;

import com.edge.app.adapter.webapi.member.dto.MeResponse;
import com.edge.app.application.auth.provided.AuthResult;

// newMember 는 이번 요청으로 회원이 생겼는지, 소셜 첫 로그인의 닉네임 설정 분기
public record AuthResponse(String accessToken, String refreshToken, MeResponse me, boolean guestMapped, boolean newMember) {
    public static AuthResponse from(AuthResult result) {
        return new AuthResponse(result.accessToken(), result.refreshToken(), MeResponse.from(result.member()),
                result.guestMapped(), result.newMember());
    }
}
