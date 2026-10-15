package com.edge.app.notification.entity;

import org.springframework.core.convert.converter.Converter;
import org.springframework.stereotype.Component;

/** 모르는 값을 COMMON400 으로 거절하는 쿼리 파라미터 kind 소문자 변환 */
@Component
public class NotiFilterConverter implements Converter<String, NotiFilter> {
    @Override
    public NotiFilter convert(String source) {
        return NotiFilter.of(source);
    }
}
