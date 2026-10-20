package com.edge.app.adapter.webapi.notification.dto;

public record UnreadCountResponse(int count) {
    public static UnreadCountResponse from(long count) {
        return new UnreadCountResponse((int) count);
    }
}
