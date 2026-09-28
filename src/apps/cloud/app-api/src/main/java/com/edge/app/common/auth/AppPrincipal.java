package com.edge.app.common.auth;

/** 회원 또는 게스트를 받는 엔드포인트의 인자. 익명이면 리졸버가 COMMON401. */
public record AppPrincipal(Kind kind, Long memberId, String deviceKey) {
    public enum Kind { MEMBER, DEVICE }

    public static AppPrincipal member(long memberId) {
        return new AppPrincipal(Kind.MEMBER, memberId, null);
    }

    public static AppPrincipal device(String deviceKey) {
        return new AppPrincipal(Kind.DEVICE, null, deviceKey);
    }

    public boolean isMember() {
        return kind == Kind.MEMBER;
    }
}
