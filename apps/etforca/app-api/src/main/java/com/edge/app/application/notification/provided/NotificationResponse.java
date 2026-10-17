package com.edge.app.application.notification.provided;

import com.edge.app.domain.notification.NotiKind;

import java.time.Instant;

public record NotificationResponse(String id, NotiKind kind, String etf, String postId, Instant time, String title,
        String body, boolean read) {
}
