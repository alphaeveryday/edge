package com.edge.app.member.dto;

import com.edge.app.member.entity.Member;

import java.time.Instant;

public record MeResponse(String nick, String handle, String email, Instant disclaimerAcceptedAt) {
    public static MeResponse from(Member member) {
        return new MeResponse(member.getNick(), member.getHandle(), member.getEmail(), member.getDisclaimerAcceptedAt());
    }
}
