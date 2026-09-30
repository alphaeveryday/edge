package com.edge.app.auth.dto;

import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

public record SignupRequest(@NotBlank @Email String email, @NotBlank @Size(min = 8) String password, @NotBlank String nick,
        @NotBlank @Pattern(regexp = "\\d{6}") String code) {
}
