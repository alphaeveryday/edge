package com.edge.app.common.auth;

/** 게스트와 익명을 COMMON401 로 거절하는 회원 전용 인자 */
public record MemberPrincipal(long memberId) {
}
