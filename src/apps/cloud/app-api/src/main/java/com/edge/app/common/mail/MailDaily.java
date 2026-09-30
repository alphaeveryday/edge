package com.edge.app.common.mail;

import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import lombok.AccessLevel;
import lombok.NoArgsConstructor;

import java.time.LocalDate;

/** 전체 하루 메일 발송 수. 날짜는 KST */
@Entity
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class MailDaily {
    @Id
    private LocalDate day;

    private int sent;
}
