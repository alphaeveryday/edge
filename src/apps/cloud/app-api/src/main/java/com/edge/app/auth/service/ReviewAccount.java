package com.edge.app.auth.service;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

/** 심사용 계정. 지정 이메일 하나만 고정 코드, 메일 미발송 */
@Component
public class ReviewAccount {
    static final String CODE = "123456";

    private final String email;

    public ReviewAccount(@Value("${app.review.email:}") String email) {
        this.email = email;
    }

    public boolean is(String address) {
        return !email.isBlank() && email.equalsIgnoreCase(address);
    }
}
