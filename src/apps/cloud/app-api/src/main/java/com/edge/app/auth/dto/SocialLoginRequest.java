package com.edge.app.auth.dto;

import com.edge.app.member.entity.Provider;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;

// nonce 는 카카오 필수, 그 외 미사용
public record SocialLoginRequest(@NotNull Provider provider, @NotBlank String idToken, String nonce) {
}
