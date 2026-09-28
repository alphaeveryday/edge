package com.edge.app.notification.entity;

import org.springframework.core.convert.converter.Converter;
import org.springframework.stereotype.Component;

/** 쿼리 파라미터 kind 를 소문자 값으로 받는다. 모르는 값은 변환 실패로 COMMON400. */
@Component
public class NotiFilterConverter implements Converter<String, NotiFilter> {
    @Override
    public NotiFilter convert(String source) {
        return NotiFilter.of(source);
    }
}
