package com.edge.app.common.auth;

/** 회원 전용 엔드포인트의 인자. 게스트·익명이면 리졸버가 COMMON401. */
public record MemberPrincipal(long memberId) {
}
