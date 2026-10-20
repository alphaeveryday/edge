package com.edge.app.application.onboarding;

import com.edge.app.application.member.provided.AppPrincipal;
import com.edge.app.application.member.required.PrincipalRepository;
import com.edge.app.application.onboarding.provided.OnboardingCompleteRequest;
import com.edge.app.application.onboarding.provided.OnboardingCompleter;
import com.edge.app.application.onboarding.required.PrincipalThemeRepository;
import com.edge.app.domain.onboarding.OnboardingCompleted;
import com.edge.app.domain.onboarding.PrincipalTheme;
import lombok.RequiredArgsConstructor;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.LinkedHashSet;
import java.util.List;

@Service
@RequiredArgsConstructor
public class OnboardingService implements OnboardingCompleter {
    private final PrincipalRepository principalRepository;
    private final PrincipalThemeRepository themeRepository;
    private final ApplicationEventPublisher eventPublisher;

    // 테마 교체
    // 이벤트 기반의 ETF 기본 그룹 추가
    @Override
    @Transactional
    public void complete(AppPrincipal principal, OnboardingCompleteRequest request) {
        long principalId = principalRepository.resolve(principal);
        themeRepository.deleteByPrincipalId(principalId);
        themeRepository.flush();
        for (String theme : new LinkedHashSet<>(request.themes())) {
            themeRepository.save(PrincipalTheme.of(principalId, theme));
        }
        eventPublisher.publishEvent(new OnboardingCompleted(principalId, List.copyOf(new LinkedHashSet<>(request.etfs()))));
    }
}
