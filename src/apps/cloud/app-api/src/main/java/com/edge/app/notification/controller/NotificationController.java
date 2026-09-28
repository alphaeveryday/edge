package com.edge.app.notification.controller;

import com.edge.app.common.auth.AppPrincipal;
import com.edge.app.common.cursor.Cursor;
import com.edge.app.common.cursor.PageResponse;
import com.edge.app.notification.dto.NotificationResponse;
import com.edge.app.notification.dto.UnreadCountResponse;
import com.edge.app.notification.entity.NotiFilter;
import com.edge.app.notification.service.NotificationService;
import com.edge.common.apipayload.ApiResponse;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/notifications")
@RequiredArgsConstructor
public class NotificationController {
    private final NotificationService notificationService;

    @GetMapping
    public ApiResponse<PageResponse<NotificationResponse>> notificationList(AppPrincipal principal,
            @RequestParam(required = false) NotiFilter kind, @RequestParam(required = false) String cursor,
            @RequestParam(defaultValue = "20") @Min(1) @Max(100) int size) {
        return ApiResponse.onSuccess(notificationService.list(principal, kind, cursor == null ? null : Cursor.decode(cursor), size));
    }

    @GetMapping("/unread-count")
    public ApiResponse<UnreadCountResponse> notificationUnread(AppPrincipal principal) {
        return ApiResponse.onSuccess(notificationService.unread(principal));
    }

    @PostMapping("/{id}/read")
    public ApiResponse<Void> notificationRead(AppPrincipal principal, @PathVariable String id) {
        notificationService.read(principal, id);
        return ApiResponse.onSuccess(null);
    }

    @PostMapping("/read-all")
    public ApiResponse<Void> notificationReadAll(AppPrincipal principal) {
        notificationService.readAll(principal);
        return ApiResponse.onSuccess(null);
    }
}
