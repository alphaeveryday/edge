package com.edge.app.auth.dto;

import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

public record PasswordResetConfirmRequest(@NotBlank @Email String email, @NotBlank @Pattern(regexp = "\\d{6}") String code,
        @NotBlank @Size(min = 8) String newPassword) {
}
