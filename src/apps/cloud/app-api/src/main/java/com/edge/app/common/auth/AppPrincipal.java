package com.edge.app.common.auth;

/** 회원 또는 게스트 인자. 익명은 COMMON401 */
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
