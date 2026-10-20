package com.edge.app.adapter.webapi.notification.dto;

import com.edge.app.domain.notification.NotiKind;
import com.edge.app.domain.notification.Notification;

import java.time.Instant;

public record NotificationResponse(String id, NotiKind kind, String etf, String postId, Instant time, String title,
        String body, boolean read) {
    public static NotificationResponse from(Notification n) {
        return new NotificationResponse(Long.toString(n.getId()), n.getKind(), n.getEtfCode(),
                n.getPostId() == null ? null : Long.toString(n.getPostId()), n.getCreatedAt(), n.getTitle(), n.getBody(),
                n.getReadAt() != null);
    }
}
