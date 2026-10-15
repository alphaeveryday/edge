package com.edge.app.notification.controller;

import com.edge.app.common.auth.AppPrincipal;
import com.edge.app.common.cursor.Cursor;
import com.edge.app.common.cursor.PageResponse;
import com.edge.app.notification.dto.NotificationResponse;
import com.edge.app.notification.dto.PushTokenRequest;
import com.edge.app.notification.dto.UnreadCountResponse;
import com.edge.app.notification.entity.NotiFilter;
import com.edge.app.notification.service.NotificationService;
import com.edge.app.notification.service.PushService;
import com.edge.common.apipayload.ApiResponse;
import jakarta.validation.Valid;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/notifications")
@RequiredArgsConstructor
public class NotificationController {
    private final NotificationService notificationService;
    private final PushService pushService;

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

    // 회원도 기기 헤더 필수, 토큰은 기기 단위
    @PutMapping("/push-token")
    public ApiResponse<Void> notificationPushToken(AppPrincipal principal,
            @RequestHeader("X-Device-Id") @NotBlank @Size(max = 64) String deviceKey, @RequestBody @Valid PushTokenRequest request) {
        pushService.register(principal, deviceKey.trim(), request.token());
        return ApiResponse.onSuccess(null);
    }
}
