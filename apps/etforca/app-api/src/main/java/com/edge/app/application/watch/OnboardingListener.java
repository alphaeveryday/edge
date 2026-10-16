package com.edge.app.application.watch;

import com.edge.app.domain.onboarding.OnboardingCompleted;
import lombok.RequiredArgsConstructor;
import org.springframework.context.event.EventListener;
import org.springframework.stereotype.Component;

@Component
@RequiredArgsConstructor
public class OnboardingListener {
    private final WatchService watchService;

    @EventListener
    public void on(OnboardingCompleted event) {
        watchService.addToBase(event.principalId(), event.etfs());
    }
}
