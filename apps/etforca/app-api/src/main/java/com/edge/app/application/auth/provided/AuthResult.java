package com.edge.app.application.auth.provided;

import com.edge.app.domain.member.Member;

public record AuthResult(String accessToken, String refreshToken, Member member, boolean guestMapped, boolean newMember) {
}
