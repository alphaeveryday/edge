package com.edge.app.watch.event;

import com.edge.app.onboarding.event.OnboardingCompleted;
import com.edge.app.watch.service.WatchService;
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
