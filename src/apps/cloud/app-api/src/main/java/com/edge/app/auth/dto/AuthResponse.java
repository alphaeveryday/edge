package com.edge.app.auth.dto;

import com.edge.app.member.dto.MeResponse;

public record AuthResponse(String accessToken, String refreshToken, MeResponse me, boolean guestMapped) {
}
