package com.edge.app.notification.dto;

import com.edge.app.notification.entity.NotiKind;

import java.time.Instant;

public record NotificationResponse(String id, NotiKind kind, String etf, String postId, Instant time, String title,
        String body, boolean read) {
}
