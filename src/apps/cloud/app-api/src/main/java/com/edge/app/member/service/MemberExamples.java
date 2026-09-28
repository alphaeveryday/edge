package com.edge.app.member.service;

import com.edge.app.member.dto.MeResponse;

import java.time.Instant;

/** 스텁 고정값. 실구현 때 삭제. */
public final class MemberExamples {
    private static final Instant AT = Instant.parse("2026-01-01T00:00:00Z");

    private MemberExamples() {
    }

    public static MeResponse me() {
        return new MeResponse("nick", "@handle", "member@example.com", AT);
    }
}
