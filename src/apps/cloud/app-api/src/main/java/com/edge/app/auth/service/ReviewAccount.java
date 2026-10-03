package com.edge.app.auth.service;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

/** 지정 이메일 하나에 메일 없이 고정 코드를 쓰는 심사용 계정 */
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
