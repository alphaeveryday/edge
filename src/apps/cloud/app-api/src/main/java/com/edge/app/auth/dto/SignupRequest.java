package com.edge.app.auth.dto;

import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.NotBlank;

public record SignupRequest(@NotBlank @Email String email, @NotBlank String password, @NotBlank String nick,
        @NotBlank String code) {
}
