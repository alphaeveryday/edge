package com.edge.app.application.notification.provided;

import com.edge.app.application.member.provided.AppPrincipal;

/**
 * 알림 읽음 처리
 */
public interface NotificationModifier {
    void read(AppPrincipal principal, String id);

    void readAll(AppPrincipal principal);
}
