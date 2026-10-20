package com.edge.app.application.onboarding.provided;

import com.edge.app.application.member.provided.AppPrincipal;

/**
 * 온보딩 완료
 */
public interface OnboardingCompleter {
    void complete(AppPrincipal principal, OnboardingCompleteRequest request);
}
