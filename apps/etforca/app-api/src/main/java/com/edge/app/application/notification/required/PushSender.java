package com.edge.app.application.notification.required;

import java.util.List;
import java.util.Map;

/**
 * 푸시 발송
 * - 예외 없는 실패 처리와 무효 토큰의 반환
 */
public interface PushSender {
    List<String> send(List<String> tokens, String title, String body, Map<String, String> data);
}
