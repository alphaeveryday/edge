package com.edge.app.auth.dto;

import com.edge.app.member.entity.Provider;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;

public record SocialLoginRequest(@NotNull Provider provider, @NotBlank String idToken) {
}
