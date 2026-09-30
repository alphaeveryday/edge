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

/** e2e·테스트 가입이 실서버에서 존재하지 않는 주소로 메일을 보내 반송이 쌓이면 발신 평판이 떨어진다 */
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
