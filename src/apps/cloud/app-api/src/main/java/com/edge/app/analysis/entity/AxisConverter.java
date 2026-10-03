package com.edge.app.analysis.entity;

import org.springframework.core.convert.converter.Converter;
import org.springframework.stereotype.Component;

/** 모르는 값을 COMMON400 으로 거절하는 경로 변수 axis 소문자 변환 */
@Component
public class AxisConverter implements Converter<String, Axis> {
    @Override
    public Axis convert(String source) {
        return Axis.of(source);
    }
}
