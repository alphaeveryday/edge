package com.edge.app.onboarding.service;

import com.edge.app.common.auth.AppPrincipal;
import com.edge.app.member.repository.PrincipalRepository;
import com.edge.app.onboarding.dto.OnboardingCompleteRequest;
import com.edge.app.onboarding.entity.PrincipalTheme;
import com.edge.app.onboarding.event.OnboardingCompleted;
import com.edge.app.onboarding.repository.PrincipalThemeRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.LinkedHashSet;
import java.util.List;

@Service
@RequiredArgsConstructor
public class OnboardingService {
    private final PrincipalRepository principalRepository;
    private final PrincipalThemeRepository themeRepository;
    private final ApplicationEventPublisher eventPublisher;

    // 테마는 교체, ETF 는 기본 그룹에 추가(watch 가 이벤트로 처리).
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
