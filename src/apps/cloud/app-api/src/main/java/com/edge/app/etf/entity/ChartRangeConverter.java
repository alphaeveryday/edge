package com.edge.app.etf.entity;

import org.springframework.core.convert.converter.Converter;
import org.springframework.stereotype.Component;

/** 쿼리 파라미터 range 를 계약값으로 받는다. 모르는 값은 변환 실패로 COMMON400. */
@Component
public class ChartRangeConverter implements Converter<String, ChartRange> {
    @Override
    public ChartRange convert(String source) {
        return ChartRange.of(source);
    }
}
