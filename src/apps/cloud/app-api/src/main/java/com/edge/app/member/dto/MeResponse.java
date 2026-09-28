package com.edge.app.member.dto;

import java.time.Instant;

public record MeResponse(String nick, String handle, String email, Instant disclaimerAcceptedAt) {
}
