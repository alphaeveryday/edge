package com.edge.app.theme.entity;

import org.springframework.core.convert.converter.Converter;
import org.springframework.stereotype.Component;

/** 쿼리 파라미터 dir 를 소문자 값으로 받는다. 모르는 값은 변환 실패로 COMMON400. */
@Component
public class FeedDirConverter implements Converter<String, FeedDir> {
    @Override
    public FeedDir convert(String source) {
        return FeedDir.of(source);
    }
}
