package com.edge.app.auth.dto;

import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.NotBlank;

public record SignupCodeRequest(@NotBlank @Email String email) {
}
