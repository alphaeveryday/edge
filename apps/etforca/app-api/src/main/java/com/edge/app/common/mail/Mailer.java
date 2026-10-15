package com.edge.app.common.mail;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.mail.SimpleMailMessage;
import org.springframework.mail.javamail.JavaMailSender;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Component;

/**
  * SMTP 설정이 있을 때 한정의 실제 발송
  * SMTP 설정이 없을 때의 로그 기록
  * 반송 방지를 위한 예약 도메인 example.com 의 설정 무관 로그 기록
  * e2e 의 로그 기반 코드 확인
  * 응답 시간으로 가입 여부가 드러나지 않게 하는 비동기 발송
  * 발송 실패의 로그 기록
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class Mailer {
    private final ObjectProvider<JavaMailSender> smtp;

    @Value("${spring.mail.username:}")
    private String from;

    @Async
    public void send(String to, String subject, String text) {
        JavaMailSender sender = smtp.getIfAvailable();
        if (sender == null || to.endsWith("@example.com")) {
            // 줄마다 이벤트를 나누는 CloudWatch 대비 한 줄 기록
            log.info("mail(fake) to={} subject={} text={}", to, subject, text.replace('\n', ' '));
            return;
        }
        SimpleMailMessage message = new SimpleMailMessage();
        message.setFrom("ETF Orca <" + from + ">");
        message.setTo(to);
        message.setSubject(subject);
        message.setText(text);
        try {
            sender.send(message);
        } catch (Exception e) {
            log.warn("mail send failed to={} subject={}", to, subject, e);
        }
    }
}
