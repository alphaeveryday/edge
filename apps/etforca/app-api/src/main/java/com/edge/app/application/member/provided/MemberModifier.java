package com.edge.app.application.member.provided;

import com.edge.app.domain.member.Member;

/**
 * 회원 정보 변경과 탈퇴
 */
public interface MemberModifier {
    Member update(long memberId, MeUpdateRequest request);

    Member acceptDisclaimer(long memberId);

    void deleteAccount(long memberId);
}
