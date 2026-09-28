package com.edge.app.issue.entity;

import org.springframework.core.convert.converter.Converter;
import org.springframework.stereotype.Component;

/** 쿼리 파라미터 tab 을 소문자 값으로 받는다. 모르는 값은 변환 실패로 COMMON400. */
@Component
public class IssueTabConverter implements Converter<String, IssueTab> {
    @Override
    public IssueTab convert(String source) {
        return IssueTab.of(source);
    }
}
