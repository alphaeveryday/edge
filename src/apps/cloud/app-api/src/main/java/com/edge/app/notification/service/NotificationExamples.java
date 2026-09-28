package com.edge.app.notification.service;

import com.edge.app.notification.dto.NotificationResponse;
import com.edge.app.notification.entity.NotiKind;

import java.time.Instant;

/** 스텁 고정값. 실구현 때 삭제. */
final class NotificationExamples {
    private static final Instant AT = Instant.parse("2026-01-01T00:00:00Z");

    private NotificationExamples() {
    }

    static NotificationResponse notification() {
        return new NotificationResponse("id", NotiKind.WATCH, "000000", "postId", AT, "title", "body", false);
    }
}
