package com.edge.app.application.mail.required;

/**
 * 메일 발송
 * - 비동기 발송과 예외 없는 실패 처리
 */
public interface EmailSender {
    void send(String to, String subject, String text);
}
