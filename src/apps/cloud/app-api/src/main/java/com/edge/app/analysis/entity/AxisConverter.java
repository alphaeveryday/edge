package com.edge.app.analysis.entity;

import org.springframework.core.convert.converter.Converter;
import org.springframework.stereotype.Component;

/** 경로 변수 {axis} 를 소문자 값으로 받는다. 모르는 값은 변환 실패로 COMMON400. */
@Component
public class AxisConverter implements Converter<String, Axis> {
    @Override
    public Axis convert(String source) {
        return Axis.of(source);
    }
}
