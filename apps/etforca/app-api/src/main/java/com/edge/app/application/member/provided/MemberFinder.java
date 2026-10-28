package com.edge.app.application.member.provided;

import com.edge.app.domain.member.Member;

/**
 * 회원 조회
 * - 탈퇴 회원은 유효 토큰이어도 COMMON401
 */
public interface MemberFinder {
    Member find(long memberId);

    boolean isActive(long memberId);
}
