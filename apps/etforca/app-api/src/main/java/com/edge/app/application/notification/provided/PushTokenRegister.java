package com.edge.app.application.notification.provided;

import com.edge.app.application.member.provided.AppPrincipal;

/**
 * 기기 단위 푸시 토큰 등록
 */
public interface PushTokenRegister {
    void register(AppPrincipal principal, String deviceKey, String token);
}
