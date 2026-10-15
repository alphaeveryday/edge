package com.edge.app.auth.dto;

import com.edge.app.member.dto.MeResponse;

// newMember 는 이번 요청으로 회원이 생겼는지, 소셜 첫 로그인의 닉네임 설정 분기
public record AuthResponse(String accessToken, String refreshToken, MeResponse me, boolean guestMapped, boolean newMember) {
}
