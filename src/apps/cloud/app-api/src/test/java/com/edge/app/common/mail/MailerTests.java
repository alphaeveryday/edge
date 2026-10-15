package com.edge.app.common.mail;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.mail.SimpleMailMessage;
import org.springframework.mail.javamail.JavaMailSender;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/** 실서버 테스트 가입의 없는 주소 발송으로 쌓이는 반송과 발신 평판 하락 방지 */
class MailerTests {
    @Test
    void reservedTestDomainIsLoggedNotSent() {
        JavaMailSender smtp = mock(JavaMailSender.class);
        @SuppressWarnings("unchecked")
        ObjectProvider<JavaMailSender> provider = mock(ObjectProvider.class);
        when(provider.getIfAvailable()).thenReturn(smtp);
        Mailer mailer = new Mailer(provider);

        mailer.send("e2e-1@example.com", "s", "t");
        verify(smtp, never()).send(any(SimpleMailMessage.class));
        mailer.send("someone@gmail.com", "s", "t");
        verify(smtp, times(1)).send(any(SimpleMailMessage.class));
    }
}
