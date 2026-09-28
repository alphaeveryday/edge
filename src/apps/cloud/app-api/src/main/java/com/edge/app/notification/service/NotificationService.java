package com.edge.app.notification.service;

import com.edge.app.common.auth.AppPrincipal;
import com.edge.app.common.cursor.Cursor;
import com.edge.app.common.cursor.PageResponse;
import com.edge.app.notification.dto.NotificationResponse;
import com.edge.app.notification.dto.UnreadCountResponse;
import com.edge.app.notification.entity.NotiKind;
import org.springframework.stereotype.Service;

import java.time.Instant;
import java.util.List;

/** 스텁. */
@Service
public class NotificationService {
    private static final Instant AT = Instant.parse("2026-01-01T00:00:00Z");

    public PageResponse<NotificationResponse> list(AppPrincipal principal, String kind, Cursor cursor, int size) {
        return new PageResponse<>(List.of(
                new NotificationResponse("id", NotiKind.WATCH, "000000", "postId", AT, "title", "body", false)), null);
    }

    public UnreadCountResponse unread(AppPrincipal principal) {
        return new UnreadCountResponse(0);
    }

    public void read(AppPrincipal principal, String id) {
    }

    public void readAll(AppPrincipal principal) {
    }
}
