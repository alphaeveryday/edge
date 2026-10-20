package com.edge.app.application.notification.provided;

import com.edge.app.application.member.provided.AppPrincipal;
import com.edge.app.domain.notification.NotiFilter;
import com.edge.app.domain.notification.Notification;
import com.edge.app.support.cursor.Cursor;
import com.edge.app.support.cursor.PageResponse;

/**
 * 알림 조회
 * - 최신순 커서 페이지와 안 읽은 알림 수
 */
public interface NotificationFinder {
    PageResponse<Notification> list(AppPrincipal principal, NotiFilter filter, Cursor cursor, int size);

    long unread(AppPrincipal principal);
}
