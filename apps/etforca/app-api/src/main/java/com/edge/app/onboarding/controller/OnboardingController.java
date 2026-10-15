package com.edge.app.onboarding.controller;

import com.edge.app.common.auth.AppPrincipal;
import com.edge.app.onboarding.dto.OnboardingCompleteRequest;
import com.edge.app.onboarding.service.OnboardingService;
import com.edge.common.apipayload.ApiResponse;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/onboarding")
@RequiredArgsConstructor
public class OnboardingController {
    private final OnboardingService onboardingService;

    @PostMapping("/complete")
    public ApiResponse<Void> onboardingComplete(AppPrincipal principal, @RequestBody @Valid OnboardingCompleteRequest request) {
        onboardingService.complete(principal, request);
        return ApiResponse.onSuccess(null);
    }
}
