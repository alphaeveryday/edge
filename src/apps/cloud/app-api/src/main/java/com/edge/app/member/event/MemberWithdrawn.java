package com.edge.app.member.event;

/** 회원 탈퇴. 도메인별 회원 행 삭제용, 같은 트랜잭션 */
public record MemberWithdrawn(long memberId) {
}
