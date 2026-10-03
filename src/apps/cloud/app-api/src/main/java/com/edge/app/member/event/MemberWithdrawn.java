package com.edge.app.member.event;

/** 같은 트랜잭션 안의 도메인별 회원 행 삭제용 회원 탈퇴 이벤트 */
public record MemberWithdrawn(long memberId) {
}
