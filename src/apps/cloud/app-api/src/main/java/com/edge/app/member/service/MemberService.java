package com.edge.app.member.service;

import com.edge.app.member.dto.MeResponse;
import com.edge.app.member.dto.MeUpdateRequest;
import org.springframework.stereotype.Service;

import java.time.Instant;

/** 스텁. */
@Service
public class MemberService {
    private static final Instant AT = Instant.parse("2026-01-01T00:00:00Z");

    public MeResponse me(long memberId) {
        return new MeResponse("nick", "@handle", "member@example.com", AT);
    }

    public MeResponse update(long memberId, MeUpdateRequest request) {
        return me(memberId);
    }

    public void deleteAccount(long memberId) {
    }

    public MeResponse acceptDisclaimer(long memberId) {
        return me(memberId);
    }
}
