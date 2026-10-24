package com.edge.app.adapter.webapi.notification;

import com.edge.app.adapter.webapi.notification.dto.NotificationResponse;
import com.edge.app.adapter.webapi.notification.dto.PushTokenRequest;
import com.edge.app.adapter.webapi.notification.dto.UnreadCountResponse;
import com.edge.app.application.member.provided.AppPrincipal;
import com.edge.app.application.notification.provided.NotificationFinder;
import com.edge.app.application.notification.provided.NotificationModifier;
import com.edge.app.application.notification.provided.PushTokenRegister;
import com.edge.app.domain.notification.NotiFilter;
import com.edge.app.domain.notification.Notification;
import com.edge.app.support.cursor.Cursor;
import com.edge.app.support.cursor.PageResponse;
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
    private final NotificationFinder notificationFinder;
    private final NotificationModifier notificationModifier;
    private final PushTokenRegister pushTokenRegister;

    @GetMapping
    public ApiResponse<PageResponse<NotificationResponse>> notificationList(AppPrincipal principal,
            @RequestParam(required = false) NotiFilter kind, @RequestParam(required = false) String cursor,
            @RequestParam(defaultValue = "20") @Min(1) @Max(100) int size) {
        PageResponse<Notification> page = notificationFinder.list(principal, kind, cursor == null ? null : Cursor.decode(cursor), size);
        return ApiResponse.onSuccess(new PageResponse<>(page.items().stream().map(NotificationResponse::from).toList(), page.nextCursor()));
    }

    @GetMapping("/unread-count")
    public ApiResponse<UnreadCountResponse> notificationUnread(AppPrincipal principal) {
        return ApiResponse.onSuccess(UnreadCountResponse.from(notificationFinder.unread(principal)));
    }

    @PostMapping("/{id}/read")
    public ApiResponse<Void> notificationRead(AppPrincipal principal, @PathVariable String id) {
        notificationModifier.read(principal, id);
        return ApiResponse.onSuccess(null);
    }

    @PostMapping("/read-all")
    public ApiResponse<Void> notificationReadAll(AppPrincipal principal) {
        notificationModifier.readAll(principal);
        return ApiResponse.onSuccess(null);
    }

    // 회원도 기기 헤더 필수, 토큰은 기기 단위
    @PutMapping("/push-token")
    public ApiResponse<Void> notificationPushToken(AppPrincipal principal,
            @RequestHeader("X-Device-Id") @NotBlank @Size(max = 64) String deviceKey, @RequestBody @Valid PushTokenRequest request) {
        pushTokenRegister.register(principal, deviceKey.trim(), request.token());
        return ApiResponse.onSuccess(null);
    }
}
