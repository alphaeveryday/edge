package com.edge.app.notification.service;

import com.edge.app.common.auth.AppPrincipal;
import com.edge.app.common.cursor.Cursor;
import com.edge.app.common.cursor.PageResponse;
import com.edge.app.notification.dto.NotificationResponse;
import com.edge.app.notification.dto.UnreadCountResponse;
import org.springframework.stereotype.Service;

import java.util.List;

/** 스텁. */
@Service
public class NotificationService {
    public PageResponse<NotificationResponse> list(AppPrincipal principal, String kind, Cursor cursor, int size) {
        return new PageResponse<>(List.of(NotificationExamples.notification()), null);
    }

    public UnreadCountResponse unread(AppPrincipal principal) {
        return new UnreadCountResponse(0);
    }

    public void read(AppPrincipal principal, String id) {
    }

    public void readAll(AppPrincipal principal) {
    }
}
