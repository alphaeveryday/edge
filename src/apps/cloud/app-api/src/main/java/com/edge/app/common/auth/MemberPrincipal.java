package com.edge.app.common.auth;

/** 회원 전용 인자. 게스트·익명은 COMMON401 */
public record MemberPrincipal(long memberId) {
}
