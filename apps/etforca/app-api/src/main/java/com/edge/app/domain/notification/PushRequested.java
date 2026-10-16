package com.edge.app.domain.notification;

import java.util.Map;

/** 커밋 뒤 principal 의 푸시 토큰 전부로 발송 */
public record PushRequested(long principalId, String title, String body, Map<String, String> data) {
}
