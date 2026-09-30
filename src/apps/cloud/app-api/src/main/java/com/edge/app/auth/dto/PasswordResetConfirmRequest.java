package com.edge.app.auth.dto;

import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.NotBlank;

public record PasswordResetConfirmRequest(@NotBlank @Email String email, @NotBlank String code,
        @NotBlank String newPassword) {
}
