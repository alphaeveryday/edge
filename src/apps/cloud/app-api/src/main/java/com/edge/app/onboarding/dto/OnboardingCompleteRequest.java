package com.edge.app.onboarding.dto;

import jakarta.validation.constraints.NotNull;

import java.util.List;

public record OnboardingCompleteRequest(@NotNull List<String> themes, @NotNull List<String> etfs) {
}
