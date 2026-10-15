package com.edge.app.common.mail;

import com.edge.app.common.AppErrorStatus;
import com.edge.common.exception.GeneralException;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;

import java.time.LocalDate;
import java.time.ZoneId;

/**
 * Gmail 하루 한도 약 500통 보호용 전체 하루 발송 상한
 * 상한 초과 시 코드 메일 한정 거절
 */
@Component
@RequiredArgsConstructor
public class MailQuota {
    private static final int DAILY_LIMIT = 300;
    private static final ZoneId KST = ZoneId.of("Asia/Seoul");

    private final MailDailyRepository repository;

    // 거절 시 호출 트랜잭션 롤백으로 증가분도 취소
    public void take() {
        if (count() > DAILY_LIMIT) {
            throw new GeneralException(AppErrorStatus.AUTH_MAIL_LIMIT);
        }
    }

    public int count() {
        return repository.increment(LocalDate.now(KST));
    }
}
