package com.edge.app.adapter.webapi.onboarding;

import com.edge.app.application.member.provided.AppPrincipal;
import com.edge.app.application.onboarding.provided.OnboardingCompleteRequest;
import com.edge.app.application.onboarding.provided.OnboardingCompleter;
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
    private final OnboardingCompleter onboardingCompleter;

    @PostMapping("/complete")
    public ApiResponse<Void> onboardingComplete(AppPrincipal principal, @RequestBody @Valid OnboardingCompleteRequest request) {
        onboardingCompleter.complete(principal, request);
        return ApiResponse.onSuccess(null);
    }
}
