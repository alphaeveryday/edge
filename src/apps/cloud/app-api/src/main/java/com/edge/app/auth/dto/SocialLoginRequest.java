package com.edge.app.auth.dto;

import jakarta.validation.constraints.NotBlank;

public record SocialLoginRequest(@NotBlank String provider, @NotBlank String idToken) {
}
