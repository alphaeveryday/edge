package com.edge.app.notification.event;

import com.edge.app.notification.service.PushService;
import lombok.RequiredArgsConstructor;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Component;
import org.springframework.transaction.event.TransactionalEventListener;

/**
 * 알림 저장 커밋 뒤 비동기 발송
 * 도착 보장 없음, 알림함이 기록
 */
@Component
@RequiredArgsConstructor
public class PushListener {
    private final PushService pushService;

    @Async
    @TransactionalEventListener
    public void on(PushRequested event) {
        pushService.send(event.principalId(), event.title(), event.body(), event.data());
    }
}
